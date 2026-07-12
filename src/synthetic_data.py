"""Synthetic Data Generation — employee home locations by Hamburg/Norderstedt Stadtteil."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from geopy.distance import geodesic
from shapely.geometry import Point

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GEOJSON_PATH = DATA_DIR / "hamburg_stadtteile.geojson"
OUTPUT_PATH = DATA_DIR / "employees_synthetic.csv"

COMPANY_LOCATION = (53.707, 10.003)  # (lat, lon) — Norderstedt

WEIGHT_POP = 0.4
WEIGHT_RESIDENTIAL = 0.2
WEIGHT_TRANSPORT = 0.2
WEIGHT_DISTANCE = 0.2


# ---------------------------------------------------------------------------
# [1] Load & filter Stadtteile
# ---------------------------------------------------------------------------

def load_stadtteile(path=None):
    """Load Stadtteil GeoJSON and reproject to WGS84."""
    path = path or GEOJSON_PATH
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    else:
        gdf = gdf.to_crs(epsg=4326)
    return gdf


def filter_residential(gdf):
    """Keep only residential Stadtteile; exclude port, industrial, airport."""
    mask = (
        (gdf["is_residential"] == 1)
        & (gdf["near_port_industrial"] == 0)
    )
    return gdf.loc[mask].copy()


# ---------------------------------------------------------------------------
# [3] Score & weight helpers
# ---------------------------------------------------------------------------

def normalize(series):
    """Min-max normalize a series to [0, 1]."""
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(0.5, index=series.index)
    return (series - lo) / (hi - lo)


def compute_residential_score(gdf):
    """Combine residential attributes into a 0–1 score."""
    raw = (
        gdf["is_residential"] * 0.35
        + gdf["has_school"] * 0.20
        + gdf["has_supermarket"] * 0.20
        + gdf["has_park"] * 0.15
        + (1 - gdf["near_port_industrial"]) * 0.10
    )
    return normalize(raw)


def compute_transport_score(gdf):
    """Combine PT stop counts into a 0–1 score."""
    raw = (
        gdf["ubahn_stops"] * 3
        + gdf["sbahn_stops"] * 2
        + gdf["bus_stops"] * 1
    )
    return normalize(raw)


def compute_distance_km(centroid, company=COMPANY_LOCATION):
    """Geodesic distance from a centroid to the company (km)."""
    return geodesic(
        (centroid.y, centroid.x),
        company,
    ).kilometers


def compute_distance_score(distance_km):
    """Distance preference: 1 / (distance_km + 1)."""
    return 1.0 / (distance_km + 1.0)


def compute_stadtteil_weights(gdf):
    """Calculate composite sampling weight for each Stadtteil."""
    gdf = gdf.copy()
    projected = gdf.to_crs(epsg=25832)
    gdf["centroid"] = projected.geometry.centroid.to_crs(epsg=4326)

    gdf["pop_norm"] = normalize(gdf["population_density"].astype(float))
    gdf["residential_score"] = compute_residential_score(gdf)
    gdf["transport_score"] = compute_transport_score(gdf)

    gdf["distance_to_company_km"] = gdf["centroid"].apply(compute_distance_km)
    gdf["distance_score"] = gdf["distance_to_company_km"].apply(compute_distance_score)

    gdf["weight"] = (
        WEIGHT_POP * gdf["pop_norm"]
        + WEIGHT_RESIDENTIAL * gdf["residential_score"]
        + WEIGHT_TRANSPORT * gdf["transport_score"]
        + WEIGHT_DISTANCE * gdf["distance_score"]
    )
    gdf["weight"] = gdf["weight"] / gdf["weight"].sum()
    return gdf


# ---------------------------------------------------------------------------
# [5–6] Sample Stadtteile & generate coordinates
# ---------------------------------------------------------------------------

def sample_stadtteile(gdf, n, seed=42):
    """Weighted random sampling of Stadtteile (with replacement)."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(gdf), size=n, replace=True, p=gdf["weight"].values)
    return gdf.iloc[idx].reset_index(drop=True)


def sample_point_in_polygon(polygon, rng, max_attempts=500):
    """Rejection sampling: random point inside a polygon bounding box."""
    minx, miny, maxx, maxy = polygon.bounds
    for _ in range(max_attempts):
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        pt = Point(x, y)
        if polygon.contains(pt):
            return pt
    return polygon.representative_point()


def generate_employees(n=500, seed=42):
    """Main pipeline: load Stadtteile → weight → sample → place employees."""
    rng = np.random.default_rng(seed)

    gdf = filter_residential(load_stadtteile())
    gdf = compute_stadtteil_weights(gdf)
    chosen = sample_stadtteile(gdf, n, seed=seed)

    records = []
    for i, (_, row) in enumerate(chosen.iterrows(), start=1):
        pt = sample_point_in_polygon(row.geometry, rng)
        dist_km = geodesic(
            (pt.y, pt.x), COMPANY_LOCATION
        ).kilometers

        records.append({
            "employee_id": i,
            "stadtteil_name": row["stadtteil_name"],
            "home_lat": round(pt.y, 6),
            "home_lon": round(pt.x, 6),
            "population_density": row["population_density"],
            "residential_score": round(row["residential_score"], 4),
            "transport_score": round(row["transport_score"], 4),
            "distance_to_company_km": round(dist_km, 2),
            "distance_to_station": np.nan,
            "pt_access_score": np.nan,
        })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# [8] Save
# ---------------------------------------------------------------------------

def save_employees(df, path=None):
    path = path or OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def main():
    df = generate_employees()
    out = save_employees(df)
    print("Generated {} employees -> {}".format(len(df), out))
    print("Stadtteile used: {}".format(df["stadtteil_name"].nunique()))
    print("Distance to company (km): min={:.1f}  median={:.1f}  max={:.1f}".format(
        df["distance_to_company_km"].min(),
        df["distance_to_company_km"].median(),
        df["distance_to_company_km"].max(),
    ))


if __name__ == "__main__":
    main()
