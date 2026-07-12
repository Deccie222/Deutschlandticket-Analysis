"""Door-to-door commute time calculation via Google Directions API."""

import logging
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from pyproj import Geod

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EMPLOYEES_PATH = DATA_DIR / "employees_synthetic.csv"

COMPANY_LAT = 53.6995
COMPANY_LON = 9.9856

DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"
MAX_RETRIES = 3
REQUEST_DELAY_S = 0.5

# Load .env if present (API keys are gitignored)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

GOOGLE_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")


def load_ors_api_keys():
    """Load ORS keys from environment (same convention as pt_connection.py)."""
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
    seen = set()
    unique = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            unique.append(k)
    return unique


ORS_API_KEYS = load_ors_api_keys()

_GEOD = Geod(ellps="WGS84")
FALLBACK_DRIVING_SPEED_KMH = 45.0

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading / saving
# ---------------------------------------------------------------------------

def load_employees(path=None):
    """Load employee CSV."""
    path = path or EMPLOYEES_PATH
    return pd.read_csv(path)


def save_employees(df, path=None):
    """Write enriched employee DataFrame back to CSV."""
    path = path or EMPLOYEES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# Google Directions API helpers
# ---------------------------------------------------------------------------

def build_directions_params(home_lat, home_lon, mode):
    """Construct query parameters for a single Directions API call."""
    return {
        "origin": "{},{}".format(home_lat, home_lon),
        "destination": "{},{}".format(COMPANY_LAT, COMPANY_LON),
        "mode": mode,
        "departure_time": int(time.time()),
        "region": "de",
        "language": "de",
    }


def call_directions_api(params, api_key, max_retries=MAX_RETRIES):
    """GET Directions API with exponential backoff (1s, 2s, 4s)."""
    params = dict(params)
    params["key"] = api_key

    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.get(DIRECTIONS_URL, params=params, timeout=30)

            if response.status_code in (429, 500, 502, 503, 504):
                wait = 2 ** attempt
                logger.warning("  HTTP %s — 正在重试 (%d/%d)，等待 %d 秒 ...",
                               response.status_code, attempt + 1, max_retries, wait)
                time.sleep(wait)
                last_error = "HTTP {}".format(response.status_code)
                continue

            response.raise_for_status()
            data = response.json()

            status = data.get("status", "UNKNOWN")
            if status in ("OVER_QUERY_LIMIT", "UNKNOWN_ERROR"):
                wait = 2 ** attempt
                logger.warning("  API status=%s — 正在重试 (%d/%d)，等待 %d 秒 ...",
                               status, attempt + 1, max_retries, wait)
                time.sleep(wait)
                last_error = status
                continue

            return data

        except requests.RequestException as exc:
            wait = 2 ** attempt
            logger.warning("  请求异常 — 正在重试 (%d/%d)，等待 %d 秒 ... (%s)",
                           attempt + 1, max_retries, wait, exc)
            time.sleep(wait)
            last_error = str(exc)

    logger.error("  Directions API 调用失败（已重试 %d 次）: %s", max_retries, last_error)
    return None


def parse_duration_seconds(response):
    """Extract route duration in seconds; return None if unavailable."""
    if response is None:
        return None
    if response.get("status") != "OK":
        return None
    try:
        return response["routes"][0]["legs"][0]["duration"]["value"]
    except (KeyError, IndexError, TypeError):
        return None


def parse_transit_transfers(response):
    """Count transit legs in the route (optional metric)."""
    if response is None or response.get("status") != "OK":
        return None
    try:
        steps = response["routes"][0]["legs"][0]["steps"]
        transit_steps = sum(
            1 for s in steps if s.get("travel_mode") == "TRANSIT"
        )
        return max(transit_steps - 1, 0)
    except (KeyError, IndexError, TypeError):
        return None


def fetch_mode_commute(home_lat, home_lon, mode, api_key):
    """Call Directions API for one mode and return parsed results."""
    params = build_directions_params(home_lat, home_lon, mode)
    logger.info("  正在调用 %s API ...", mode)
    response = call_directions_api(params, api_key)
    duration_s = parse_duration_seconds(response)
    transfers = parse_transit_transfers(response) if mode == "transit" else None
    return duration_s, transfers


# ---------------------------------------------------------------------------
# ORS fallback (when GOOGLE_MAPS_API_KEY is not configured)
# ---------------------------------------------------------------------------

def call_ors_driving_api(home_lat, home_lon, api_key, max_retries=MAX_RETRIES):
    """Fetch driving duration via ORS directions API."""
    body = {
        "coordinates": [
            [home_lon, home_lat],
            [COMPANY_LON, COMPANY_LAT],
        ],
    }
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    last_error = None

    for attempt in range(max_retries):
        try:
            response = requests.post(
                ORS_DIRECTIONS_URL, json=body, headers=headers, timeout=30,
            )
            if response.status_code in (429, 500, 502, 503, 504):
                wait = 2 ** attempt
                logger.warning(
                    "  ORS HTTP %s — 正在重试 (%d/%d)，等待 %d 秒 ...",
                    response.status_code, attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
                last_error = "HTTP {}".format(response.status_code)
                continue
            if response.status_code in (401, 403):
                return None, True
            response.raise_for_status()
            data = response.json()
            duration_s = data["routes"][0]["summary"]["duration"]
            return duration_s, False
        except (requests.RequestException, KeyError, IndexError, TypeError) as exc:
            wait = 2 ** attempt
            logger.warning(
                "  ORS 请求异常 — 正在重试 (%d/%d)，等待 %d 秒 ... (%s)",
                attempt + 1, max_retries, wait, exc,
            )
            time.sleep(wait)
            last_error = str(exc)

    logger.error("  ORS driving API 调用失败: %s", last_error)
    return None, False


def estimate_transit_minutes(driving_minutes, walking_distance_m=None):
    """Heuristic transit estimate when Google Directions is unavailable."""
    if pd.isna(driving_minutes):
        return float("nan"), None
    walk_min = (walking_distance_m / 80.0) if pd.notna(walking_distance_m) else 5.0
    transit_min = driving_minutes * 1.3 + walk_min * 2 + 12
    transfers = max(int(round(transit_min / 25)) - 1, 0)
    return round(transit_min), transfers


def geodesic_driving_minutes(home_lat, home_lon):
    """Fallback driving time from straight-line distance."""
    _, _, dist_m = _GEOD.inv(home_lon, home_lat, COMPANY_LON, COMPANY_LAT)
    dist_km = abs(dist_m) / 1000.0
    road_factor = 1.25
    return round(dist_km * road_factor / FALLBACK_DRIVING_SPEED_KMH * 60)


def fetch_ors_driving_commute(home_lat, home_lon, key_manager):
    """Fetch driving time via ORS, rotating keys on quota/rate-limit errors."""
    while key_manager.active_key:
        logger.info("  正在调用 ORS driving API (%s) ...", key_manager.label)
        duration_s, rotate = call_ors_driving_api(
            home_lat, home_lon, key_manager.active_key,
        )
        if duration_s is not None:
            key_manager.record_success()
            return duration_s
        if rotate and key_manager.rotate("配额/速率限制"):
            time.sleep(REQUEST_DELAY_S)
            continue
        break
    return None


class OrsKeyManager(object):
    """Minimal ORS key rotation for commute fallback."""

    def __init__(self, keys):
        self.keys = keys or []
        self.index = 0

    @property
    def active_key(self):
        if self.index >= len(self.keys):
            return None
        return self.keys[self.index]

    @property
    def label(self):
        return "Key {}/{}".format(self.index + 1, len(self.keys))

    def record_success(self):
        pass

    def rotate(self, reason=""):
        self.index += 1
        if self.index < len(self.keys):
            logger.warning(
                "  >> 切换 ORS API Key -> %s (%s)", self.label, reason,
            )
            return True
        logger.error("  >> 所有 ORS API Key 已用尽 (%s)", reason)
        return False


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def compute_commute_time(employees_df, api_key=None):
    """Compute driving & transit door-to-door times for every employee.

    Returns the input DataFrame enriched with:
      - driving_time_minutes
      - transit_time_minutes
      - transit_transfers (optional)
    """
    api_key = api_key if api_key is not None else GOOGLE_API_KEY
    use_google = bool(api_key)
    ors_manager = None

    if not use_google:
        if ORS_API_KEYS:
            ors_manager = OrsKeyManager(ORS_API_KEYS)
            logger.warning(
                "未设置 GOOGLE_MAPS_API_KEY — 使用 ORS driving + 启发式 transit 估算。"
            )
            logger.warning(
                "  如需真实公交路线，请在 .env 中添加 GOOGLE_MAPS_API_KEY。"
            )
        else:
            logger.error("错误: 未设置 GOOGLE_MAPS_API_KEY，且无可用 ORS_API_KEY。")
            logger.error("  请在 .env 中添加以下任一配置:")
            logger.error("    GOOGLE_MAPS_API_KEY=your_google_key")
            logger.error("    ORS_API_KEY=your_ors_key")
            sys.exit(1)

    result = employees_df.copy()
    if "driving_time_minutes" not in result.columns:
        result["driving_time_minutes"] = float("nan")
    if "transit_time_minutes" not in result.columns:
        result["transit_time_minutes"] = float("nan")
    if "transit_transfers" not in result.columns:
        result["transit_transfers"] = float("nan")

    total = len(result)
    success = 0
    skipped = 0
    ors_ok = 0
    geodesic_fallback = 0

    for idx in range(total):
        emp = result.iloc[idx]
        eid = emp["employee_id"]
        home_lat, home_lon = emp["home_lat"], emp["home_lon"]
        walking_distance_m = emp.get("walking_distance")

        if pd.notna(emp.get("driving_time_minutes")) and pd.notna(emp.get("transit_time_minutes")):
            skipped += 1
            success += 1
            continue

        logger.info("处理员工 %s (%d/%d)", eid, idx + 1, total)

        if use_google:
            drive_s, _ = fetch_mode_commute(home_lat, home_lon, "driving", api_key)
            drive_min = round(drive_s / 60) if drive_s is not None else float("nan")

            transit_s, n_transfers = fetch_mode_commute(
                home_lat, home_lon, "transit", api_key,
            )
            transit_min = round(transit_s / 60) if transit_s is not None else float("nan")
        else:
            drive_s = fetch_ors_driving_commute(home_lat, home_lon, ors_manager)
            if drive_s is not None:
                drive_min = round(drive_s / 60)
                ors_ok += 1
            else:
                drive_min = geodesic_driving_minutes(home_lat, home_lon)
                geodesic_fallback += 1
                logger.warning("  ORS 失败，使用 geodesic 估算 driving=%d min", drive_min)
            transit_min, n_transfers = estimate_transit_minutes(
                drive_min, walking_distance_m,
            )
            time.sleep(REQUEST_DELAY_S)

        if pd.notna(drive_min) or pd.notna(transit_min):
            success += 1

        result.at[result.index[idx], "driving_time_minutes"] = drive_min
        result.at[result.index[idx], "transit_time_minutes"] = transit_min
        result.at[result.index[idx], "transit_transfers"] = n_transfers

        if use_google:
            time.sleep(0.05)

    logger.info(
        "处理完成: %d/%d 名员工至少获得一条有效路线 (跳过 %d, ORS %d, geodesic %d)",
        success, total, skipped, ors_ok, geodesic_fallback,
    )
    return result


def run_commute():
    """Load employees, compute commute times, and save enriched CSV."""
    logger.info("正在加载员工数据 ...")
    employees = load_employees()
    logger.info("  共 %d 名员工", len(employees))

    enriched = compute_commute_time(employees)

    logger.info("正在保存文件 ...")
    out = save_employees(enriched)
    logger.info("已保存 -> %s", out)

    valid_drive = enriched["driving_time_minutes"].notna().sum()
    valid_transit = enriched["transit_time_minutes"].notna().sum()
    logger.info("driving_time_minutes  有效: %d/%d", valid_drive, len(enriched))
    logger.info("transit_time_minutes  有效: %d/%d", valid_transit, len(enriched))

    if valid_drive:
        logger.info("  driving  (min): median=%.0f  max=%.0f",
                     enriched["driving_time_minutes"].median(),
                     enriched["driving_time_minutes"].max())
    if valid_transit:
        logger.info("  transit  (min): median=%.0f  max=%.0f",
                     enriched["transit_time_minutes"].median(),
                     enriched["transit_time_minutes"].max())

    return enriched


# ---------------------------------------------------------------------------
# Legacy wrapper (used by notebook / summary pipeline)
# ---------------------------------------------------------------------------

def compute_commutes(employees_df=None):
    """Return a commute_results-style DataFrame from enriched employee data."""
    employees = employees_df if employees_df is not None else load_employees()

    if "transit_time_minutes" not in employees.columns:
        employees = compute_commute_time(employees)

    return pd.DataFrame({
        "employee_id": employees["employee_id"],
        "commute_time_min": employees["transit_time_minutes"],
        "driving_time_min": employees.get("driving_time_minutes"),
        "transfers": employees.get("transit_transfers"),
    })


def save_results(results, path=None):
    """Save commute results CSV (legacy downstream compatibility)."""
    path = path or DATA_DIR / "commute_results.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(path, index=False)


if __name__ == "__main__":
    run_commute()
