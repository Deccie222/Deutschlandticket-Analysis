"""Fetch public transport stations from OpenStreetMap Overpass API."""

from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_PATH = DATA_DIR / "stations.csv"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "Deutschlandticket-Analysis/1.0 (student project)"

BBOX_SOUTH = 53.4
BBOX_WEST = 9.7
BBOX_NORTH = 53.8
BBOX_EAST = 10.3

OVERPASS_QUERY = """
[out:json][timeout:25];
(
  node["railway"="station"]({south},{west},{north},{east});
  node["railway"="subway"]({south},{west},{north},{east});
  node["public_transport"="stop_position"]({south},{west},{north},{east});
  node["public_transport"="platform"]({south},{west},{north},{east});
  node["highway"="bus_stop"]({south},{west},{north},{east});
);
out body;
""".format(
    south=BBOX_SOUTH,
    west=BBOX_WEST,
    north=BBOX_NORTH,
    east=BBOX_EAST,
)


def _detect_type(tags):
    """Map OSM tags to a single station type label."""
    if tags.get("railway") == "subway":
        return "subway"
    if tags.get("railway") == "station":
        return "station"
    if tags.get("highway") == "bus_stop":
        return "bus_stop"
    if tags.get("public_transport") == "platform":
        return "platform"
    if tags.get("public_transport") == "stop_position":
        return "stop_position"
    return "unknown"


def fetch_stations(timeout=60):
    """Query Overpass API and return a DataFrame of PT stations."""
    response = requests.post(
        OVERPASS_URL,
        data=OVERPASS_QUERY,
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    rows = []
    for element in data.get("elements", []):
        if element.get("type") != "node":
            continue
        tags = element.get("tags", {})
        rows.append({
            "station_id": element["id"],
            "name": tags.get("name"),
            "lat": element["lat"],
            "lon": element["lon"],
            "type": _detect_type(tags),
        })

    df = pd.DataFrame(rows, columns=["station_id", "name", "lat", "lon", "type"])
    if not df.empty:
        df = df.drop_duplicates(subset=["station_id"]).sort_values("station_id")
        df = df.reset_index(drop=True)
    return df


def save_stations(df, path=None):
    """Write station DataFrame to CSV."""
    path = path or OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def main():
    print("Fetching stations from Overpass API (Hamburg + Norderstedt) ...")
    df = fetch_stations()
    out = save_stations(df)
    print("Saved {} stations -> {}".format(len(df), out))
    if not df.empty:
        print(df["type"].value_counts().to_string())


if __name__ == "__main__":
    main()
