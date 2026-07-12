"""Folium map visualization for employee home locations."""

import math
from pathlib import Path

import folium
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
DEFAULT_OUTPUT = OUTPUT_DIR / "employee_map.html"

COMPANY_NAME = "Johnson & Johnson Medical GmbH"
COMPANY_LAT = 53.6995
COMPANY_LON = 9.9856
MAP_CENTER = [53.55, 9.99]
MAP_ZOOM = 11

COMMUTE_COLORS = {
    "\u226430 min": "#2ecc71",
    "30\u201345 min": "#3498db",
    "45\u201360 min": "#f39c12",
    ">60 min": "#e74c3c",
    "No transit data": "#95a5a6",
}

RECOMMEND_COLOR = "#8e44ad"
MAX_STATIONS = 800
STATION_RADIUS_KM = 25


def load_stations(path=None):
    """Load public transport stations CSV if present."""
    path = path or DATA_DIR / "stations.csv"
    if not Path(path).exists():
        return None
    return pd.read_csv(path)


def _station_coords(row):
    """Return (lat, lon) from common column naming conventions."""
    if "latitude" in row and "longitude" in row:
        return row["latitude"], row["longitude"]
    if "lat" in row and "lon" in row:
        return row["lat"], row["lon"]
    raise KeyError("stations_df must contain latitude/longitude or lat/lon columns")


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _filter_nearby_stations(stations_df, max_count=MAX_STATIONS, radius_km=STATION_RADIUS_KM):
    """Keep stations near the company to keep the map readable."""
    if stations_df is None or stations_df.empty:
        return None
    rows = []
    for _, row in stations_df.iterrows():
        lat, lon = _station_coords(row)
        if _haversine_km(lat, lon, COMPANY_LAT, COMPANY_LON) <= radius_km:
            rows.append(row)
    if not rows:
        return stations_df.head(max_count)
    nearby = pd.DataFrame(rows)
    if len(nearby) > max_count:
        return nearby.sample(n=max_count, random_state=42)
    return nearby


def _commute_color(row):
    group = row.get("commute_group", "No transit data")
    return COMMUTE_COLORS.get(group, "#3186cc")


def generate_map(employees_df, stations_df=None, output_path=None):
    """Build an interactive Folium map with commute clusters and high-potential users.

    Layers:
      - Employees coloured by commute_group
      - High-potential Deutschlandticket users (Recommend)
      - Nearby HVV / PT stations
      - Company headquarters
    """
    output_path = Path(output_path) if output_path else DEFAULT_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)

    m = folium.Map(location=MAP_CENTER, zoom_start=MAP_ZOOM, tiles="CartoDB positron")

    commute_layer = folium.FeatureGroup(name="Commute-time clusters", show=True)
    recommend_layer = folium.FeatureGroup(name="High-potential D-Ticket users", show=True)
    station_layer = folium.FeatureGroup(name="Nearby PT stations", show=False)

    has_group = "commute_group" in employees_df.columns
    has_rec = "ticket_recommendation" in employees_df.columns

    for _, row in employees_df.iterrows():
        lat, lon = row["home_lat"], row["home_lon"]
        group = row.get("commute_group", "unknown") if has_group else "unknown"
        color = _commute_color(row) if has_group else "#3186cc"
        popup = "Employee {}<br>Commute: {}".format(row.get("employee_id", ""), group)
        if has_rec and pd.notna(row.get("ticket_recommendation")):
            popup += "<br>Recommendation: {}".format(row["ticket_recommendation"])

        folium.CircleMarker(
            location=[lat, lon],
            radius=4,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.75,
            weight=1,
            popup=popup,
        ).add_to(commute_layer)

        if has_rec and row.get("ticket_recommendation") == "Recommend Deutschlandticket":
            folium.CircleMarker(
                location=[lat, lon],
                radius=8,
                color=RECOMMEND_COLOR,
                fill=False,
                weight=2,
                popup="High potential: {}".format(row.get("employee_id", "")),
            ).add_to(recommend_layer)

    commute_layer.add_to(m)
    recommend_layer.add_to(m)

    nearby_stations = _filter_nearby_stations(stations_df)
    if nearby_stations is not None and not nearby_stations.empty:
        for _, row in nearby_stations.iterrows():
            lat, lon = _station_coords(row)
            name = row.get("name", "Station")
            folium.CircleMarker(
                location=[lat, lon],
                radius=2,
                color="#27ae60",
                fill=True,
                fill_color="#27ae60",
                fill_opacity=0.5,
                popup=name,
                weight=1,
            ).add_to(station_layer)
        station_layer.add_to(m)

    folium.Marker(
        location=[COMPANY_LAT, COMPANY_LON],
        popup=COMPANY_NAME,
        tooltip="Workplace",
        icon=folium.Icon(color="red", icon="building", prefix="fa"),
    ).add_to(m)

    legend_html = """
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999;
                background: white; padding: 10px 12px; border: 1px solid #ccc;
                border-radius: 6px; font-size: 12px;">
      <b>Commute groups</b><br>
      <span style="color:#2ecc71">&#9679;</span> &le;30 min<br>
      <span style="color:#3498db">&#9679;</span> 30&ndash;45 min<br>
      <span style="color:#f39c12">&#9679;</span> 45&ndash;60 min<br>
      <span style="color:#e74c3c">&#9679;</span> &gt;60 min<br>
      <span style="color:#8e44ad">&#9711;</span> Recommend D-Ticket
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    folium.LayerControl(collapsed=False).add_to(m)

    m.save(str(output_path))
    return output_path


def main():
    employees = pd.read_csv(DATA_DIR / "employees_synthetic.csv")
    stations = load_stations()
    out = generate_map(employees, stations)
    print("Map saved -> {}".format(out))


if __name__ == "__main__":
    main()
