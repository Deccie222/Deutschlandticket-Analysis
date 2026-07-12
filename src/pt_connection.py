"""Public transport connection evaluation — ORS Matrix API batch processing."""

import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from pyproj import Geod

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EMPLOYEES_PATH = DATA_DIR / "employees_synthetic.csv"
STATIONS_PATH = DATA_DIR / "stations.csv"

# Load .env if present (ORS_API_KEY is gitignored)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

ORS_MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/foot-walking"


def load_ors_api_keys():
    """Load one or more ORS API keys from environment / .env.

    Supported formats:
      ORS_API_KEY=key1
      ORS_API_KEY_2=key2
      ORS_API_KEY_3=key3
      ORS_API_KEYS=key1,key2,key3   (comma-separated, optional)
    """
    keys = []
    primary = os.environ.get("ORS_API_KEY", "").strip()
    if primary:
        keys.append(primary)

    for i in range(2, 10):
        k = os.environ.get("ORS_API_KEY_{}".format(i), "").strip()
        if k:
            keys.append(k)

    extra = os.environ.get("ORS_API_KEYS", "").strip()
    if extra:
        keys.extend(k.strip() for k in extra.split(",") if k.strip())

    # deduplicate while preserving order
    seen = set()
    unique = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            unique.append(k)
    return unique


ORS_API_KEYS = load_ors_api_keys()
ORS_API_KEY = ORS_API_KEYS[0] if ORS_API_KEYS else ""

MAX_WALKING_DISTANCE_M = 5000
PT_SCORE_DECAY_M = 800
MAX_RETRIES = 3
MAX_RATE_LIMIT_RETRIES = 3

# ORS free tier: max 3500 routes per matrix request (sources × destinations)
MAX_MATRIX_ROUTES = 3500

# Stage 1: geodesic pre-filter → top N candidates per employee
TOP_CANDIDATES = 5

# Pause between API calls to avoid 429 rate-limit
REQUEST_DELAY_S = 0.5

# Save progress every N employees (resume-friendly)
CHECKPOINT_EVERY = 25

_GEOD = Geod(ellps="WGS84")


class OrsKeyManager(object):
    """Rotate through multiple ORS API keys when quota is exhausted."""

    def __init__(self, keys):
        self.keys = keys or []
        self.index = 0
        self.usage = {i: 0 for i in range(len(self.keys))}

    @property
    def active_key(self):
        if self.index >= len(self.keys):
            return None
        return self.keys[self.index]

    @property
    def label(self):
        return "Key {}/{}".format(self.index + 1, len(self.keys))

    def record_success(self):
        self.usage[self.index] = self.usage.get(self.index, 0) + 1

    def rotate(self, reason=""):
        old = self.index + 1
        self.index += 1
        if self.index < len(self.keys):
            print("  >> 切换 API Key: {} -> Key {}/{}  {}".format(
                old, self.index + 1, len(self.keys), reason,
            ), flush=True)
            return True
        print("  >> 所有 API Key 已用尽 ({})".format(reason), flush=True)
        return False


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_employees(path=None):
    path = path or EMPLOYEES_PATH
    return pd.read_csv(path)


def load_stations(path=None):
    path = path or STATIONS_PATH
    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# Stage 1 — geodesic pre-filter (local, no API limit)
# ---------------------------------------------------------------------------

def geodesic_distances_m(home_lat, home_lon, lats, lons):
    """Vectorised WGS84 geodesic distances in metres."""
    n = len(lats)
    _, _, dist_m = _GEOD.inv(
        np.full(n, home_lon),
        np.full(n, home_lat),
        lons,
        lats,
    )
    return np.abs(dist_m)


def find_top_candidate_indices(home_lat, home_lon, st_lats, st_lons, n=TOP_CANDIDATES):
    """Return indices of the n nearest stations by straight-line distance."""
    distances = geodesic_distances_m(home_lat, home_lon, st_lats, st_lons)
    k = min(n, len(distances))
    top_idx = np.argpartition(distances, k - 1)[:k]
    return top_idx[np.argsort(distances[top_idx])]


# ---------------------------------------------------------------------------
# Stage 2 — ORS Matrix API (small batches within 3500-route limit)
# ---------------------------------------------------------------------------

def _ors_headers(api_key):
    return {"Authorization": api_key, "Content-Type": "application/json"}


class OrsQuotaExhausted(Exception):
    """Raised when all ORS API keys are exhausted."""


def _parse_ors_error(response):
    try:
        body = response.json()
        error = body.get("error", {})
        if isinstance(error, str):
            return {"message": error}
        return error if isinstance(error, dict) else {"message": str(error)}
    except ValueError:
        return {"message": response.text}


def _is_quota_exhausted(response):
    """True when the API key daily quota is used up (do not retry)."""
    if response.status_code in (401, 403):
        return True
    error = _parse_ors_error(response)
    msg = str(error.get("message", "")).lower()
    if any(w in msg for w in ("quota", "daily", "exceed", "forbidden", "not authorized", "limit")):
        return True
    return False


def _should_rotate_key(response):
    """Return True when the current key should be abandoned."""
    return _is_quota_exhausted(response) or response.status_code == 429


def call_ors_matrix(source_coords, dest_coords, api_key, max_retries=MAX_RETRIES):
    """Call ORS Matrix API. Returns (distances, rotate_key).

    Quota exhausted (401/403/quota message): rotate immediately, no retry.
    Rate limit (429): retry a few times, then rotate.
    """
    n_src = len(source_coords)
    n_dst = len(dest_coords)
    n_routes = n_src * n_dst

    if n_routes > MAX_MATRIX_ROUTES:
        print("错误: 矩阵规模 {}×{}={} 超过 ORS 上限 {}".format(
            n_src, n_dst, n_routes, MAX_MATRIX_ROUTES,
        ), flush=True)
        sys.exit(1)

    locations = source_coords + dest_coords
    body = {
        "locations": locations,
        "sources": list(range(n_src)),
        "destinations": list(range(n_src, len(locations))),
        "metrics": ["distance"],
    }

    last_error = None
    rate_limit_attempts = 0
    server_retries = max_retries
    network_retries = max_retries

    while True:
        try:
            response = requests.post(
                ORS_MATRIX_URL,
                json=body,
                headers=_ors_headers(api_key),
                timeout=120,
            )

            if response.status_code == 400:
                try:
                    msg = response.json().get("error", {}).get("message", response.text)
                except ValueError:
                    msg = response.text
                print("错误: ORS Matrix API 400 Bad Request — {}".format(msg), flush=True)
                return None, False

            # Quota used up → switch key immediately, do not retry
            if _is_quota_exhausted(response):
                error = _parse_ors_error(response)
                msg = error.get("message", "HTTP {}".format(response.status_code))
                print("  API 配额已用尽: {}".format(msg), flush=True)
                return None, True

            # Transient rate limit → limited retries on same key
            if response.status_code == 429:
                rate_limit_attempts += 1
                if rate_limit_attempts >= MAX_RATE_LIMIT_RETRIES:
                    print("  限流重试 {} 次仍失败，尝试切换 Key ...".format(
                        MAX_RATE_LIMIT_RETRIES,
                    ), flush=True)
                    return None, True
                wait = max(2 ** (rate_limit_attempts - 1), 3)
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    wait = max(wait, float(retry_after))
                print("  限流，等待 {} 秒后重试 ({}/{}) ...".format(
                    wait, rate_limit_attempts, MAX_RATE_LIMIT_RETRIES,
                ), flush=True)
                time.sleep(wait)
                continue

            if response.status_code in (500, 502, 503, 504):
                last_error = "HTTP {}".format(response.status_code)
                if server_retries <= 0:
                    print("  服务器错误: {}".format(last_error), flush=True)
                    return None, False
                server_retries -= 1
                wait = 2
                print("  服务器错误，等待 {} 秒后重试 ...".format(wait), flush=True)
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response.json()["distances"], False

        except requests.RequestException as exc:
            last_error = str(exc)
            if network_retries <= 0:
                print("  网络错误: {}".format(last_error), flush=True)
                return None, False
            network_retries -= 1
            wait = 2
            print("  网络错误，等待 {} 秒后重试 ... ({})".format(wait, exc), flush=True)
            time.sleep(wait)

    print("  ORS 调用失败: {}".format(last_error), flush=True)
    return None, False


def nearest_station_via_matrix(home_lat, home_lon, cand_indices, st_lats, st_lons,
                               st_ids, key_manager):
    """ORS walking distance; rotate keys on quota exhaustion."""
    src = [[home_lon, home_lat]]
    dst = [[st_lons[i], st_lats[i]] for i in cand_indices]

    while key_manager.active_key:
        distances, rotate = call_ors_matrix(src, dst, key_manager.active_key)
        if distances is not None:
            key_manager.record_success()
            best_j = int(np.argmin([
                d if d is not None else float("inf") for d in distances[0]
            ]))
            if distances[0][best_j] is None:
                raise OrsQuotaExhausted("ORS 返回空距离")
            return st_ids[cand_indices[best_j]], distances[0][best_j], "ors"

        if rotate:
            if not key_manager.rotate("配额/限流"):
                raise OrsQuotaExhausted("所有 ORS API Key 配额已用尽")
        else:
            raise OrsQuotaExhausted("ORS API 调用失败")

    raise OrsQuotaExhausted("无可用 ORS API Key")


def _is_processed(row):
    """True if this employee already has PT connection results."""
    for col in ("nearest_station_id", "walking_distance", "pt_access_score"):
        if col not in row.index or pd.isna(row[col]):
            return False
    return True


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_pt_access_score(walking_distance_m):
    """Exponential decay score based on walking distance."""
    if walking_distance_m > MAX_WALKING_DISTANCE_M:
        return 0.0
    return float(np.exp(-walking_distance_m / PT_SCORE_DECAY_M))


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def compute_pt_connection_matrix(employees_df, stations_df, api_keys=None):
    """Two-stage nearest-station search: geodesic pre-filter + ORS Matrix."""
    api_keys = api_keys if api_keys is not None else ORS_API_KEYS
    if not api_keys:
        print("错误: 未设置 ORS API Key。", flush=True)
        print("  在 .env 中配置:", flush=True)
        print("    ORS_API_KEY=your_key_1", flush=True)
        print("    ORS_API_KEY_2=your_key_2", flush=True)
        sys.exit(1)

    key_manager = OrsKeyManager(api_keys)
    print("已加载 {} 个 ORS API Key".format(len(api_keys)), flush=True)

    st_ids = stations_df["station_id"].values
    st_lats = stations_df["lat"].values.astype(float)
    st_lons = stations_df["lon"].values.astype(float)

    result = employees_df.copy()
    for col in ("nearest_station_id", "walking_distance", "distance_to_station", "pt_access_score"):
        if col not in result.columns:
            result[col] = np.nan

    n_emp = len(result)
    ors_ok = 0
    skipped = 0

    print("阶段 1: geodesic 预筛选 (每员工 top {} 候选站点) ...".format(
        TOP_CANDIDATES,
    ), flush=True)
    print("阶段 2: ORS Matrix API (每员工 1×{} 路线, {}) ...".format(
        TOP_CANDIDATES, key_manager.label,
    ), flush=True)

    try:
        for idx, (row_idx, emp) in enumerate(result.iterrows(), start=1):
            if _is_processed(emp):
                skipped += 1
                if skipped == 1:
                    print("  检测到已有结果，跳过已处理员工 ...", flush=True)
                continue

            home_lat, home_lon = emp["home_lat"], emp["home_lon"]
            cand_idx = find_top_candidate_indices(home_lat, home_lon, st_lats, st_lons)

            processed_so_far = idx - skipped
            if processed_so_far == 1 or processed_so_far % 25 == 0 or idx == n_emp:
                print("  员工 {}/{} (id={}) — {} ...".format(
                    idx, n_emp, emp["employee_id"], key_manager.label,
                ), flush=True)

            station_id, walk_m, method = nearest_station_via_matrix(
                home_lat, home_lon, cand_idx, st_lats, st_lons, st_ids, key_manager,
            )
            ors_ok += 1

            result.at[row_idx, "nearest_station_id"] = station_id
            result.at[row_idx, "walking_distance"] = round(walk_m, 1)
            result.at[row_idx, "distance_to_station"] = round(walk_m, 1)
            result.at[row_idx, "pt_access_score"] = round(compute_pt_access_score(walk_m), 4)

            if processed_so_far % CHECKPOINT_EVERY == 0:
                save_employees(result)
                print("  [checkpoint] 已保存进度 ({}/{})".format(idx, n_emp), flush=True)

            time.sleep(REQUEST_DELAY_S)

    except OrsQuotaExhausted as exc:
        save_employees(result)
        done = result["walking_distance"].notna().sum()
        print("", flush=True)
        print("=" * 50, flush=True)
        print("错误: {}".format(exc), flush=True)
        print("已处理 {}/{} 名员工，进度已保存。".format(done, n_emp), flush=True)
        print("请添加新的 ORS_API_KEY 到 .env 后重新运行以继续。", flush=True)
        print("=" * 50, flush=True)
        sys.exit(1)

    print("  ORS 成功: {}  跳过(已有): {}".format(ors_ok, skipped), flush=True)
    for i, count in key_manager.usage.items():
        if count:
            print("    Key {} 使用次数: {}".format(i + 1, count), flush=True)
    return result


def save_employees(df, path=None):
    path = path or EMPLOYEES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def run_pt_connection():
    """Load data, compute PT connections via Matrix API, and save CSV."""
    print("正在加载数据 ...", flush=True)
    employees = load_employees()
    stations = load_stations()
    print("  员工: {} 行, 站点: {} 行".format(len(employees), len(stations)), flush=True)

    enriched = compute_pt_connection_matrix(employees, stations)

    print("正在保存文件 ...", flush=True)
    out = save_employees(enriched)
    print("已保存 -> {}".format(out), flush=True)

    valid = enriched["walking_distance"].notna()
    print("步行距离 (m): min={:.0f}  median={:.0f}  max={:.0f}".format(
        enriched.loc[valid, "walking_distance"].min(),
        enriched.loc[valid, "walking_distance"].median(),
        enriched.loc[valid, "walking_distance"].max(),
    ), flush=True)
    print("pt_access_score: min={:.4f}  median={:.4f}  max={:.4f}".format(
        enriched["pt_access_score"].min(),
        enriched["pt_access_score"].median(),
        enriched["pt_access_score"].max(),
    ), flush=True)
    return enriched


# ---------------------------------------------------------------------------
# Legacy helpers (used by commute.py)
# ---------------------------------------------------------------------------

class PTConnection(object):
    """A public transport connection between two points."""

    def __init__(self, origin_lat, origin_lon, dest_lat, dest_lon,
                 duration_min, distance_km, transfers, monthly_cost_eur):
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.dest_lat = dest_lat
        self.dest_lon = dest_lon
        self.duration_min = duration_min
        self.distance_km = distance_km
        self.transfers = transfers
        self.monthly_cost_eur = monthly_cost_eur


def haversine_km(lat1, lon1, lat2, lon2):
    """Approximate great-circle distance in kilometres."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def estimate_connection(origin_lat, origin_lon, dest_lat, dest_lon,
                        avg_speed_kmh=25.0, cost_per_km_eur=0.15):
    """Estimate a PT connection using distance-based heuristics."""
    distance = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    duration = (distance / avg_speed_kmh) * 60
    transfers = 1 if distance > 5 else 0
    monthly_cost = distance * cost_per_km_eur * 22

    return PTConnection(
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        dest_lat=dest_lat,
        dest_lon=dest_lon,
        duration_min=round(duration, 1),
        distance_km=round(distance, 2),
        transfers=transfers,
        monthly_cost_eur=round(monthly_cost, 2),
    )


if __name__ == "__main__":
    run_pt_connection()
