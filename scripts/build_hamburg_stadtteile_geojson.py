"""Build data/hamburg_stadtteile.geojson from curated Stadtteil attributes.

Run once: python scripts/build_hamburg_stadtteile_geojson.py
"""

import json
from pathlib import Path

# (name, lat, lon, pop_density, is_residential, has_school, has_supermarket,
#  has_park, near_port_industrial, ubahn, sbahn, bus)
STADTTEILE = [
    # Hamburg — residential / mixed (excludes port, airport, heavy industrial)
    ("Altona-Altstadt", 53.549, 9.935, 4200, 1, 1, 1, 1, 0, 2, 1, 18),
    ("Altona-Nord", 53.560, 9.945, 6800, 1, 1, 1, 0, 0, 1, 1, 22),
    ("Ottensen", 53.552, 9.925, 7500, 1, 1, 1, 1, 0, 1, 1, 20),
    ("Bahrenfeld", 53.563, 9.905, 3200, 1, 1, 1, 1, 0, 0, 1, 14),
    ("Groß Flottbek", 53.577, 9.895, 2100, 1, 1, 1, 1, 0, 0, 0, 8),
    ("Othmarschen", 53.552, 9.885, 2800, 1, 1, 1, 1, 0, 0, 0, 10),
    ("Blankenese", 53.558, 9.810, 1800, 1, 1, 1, 1, 0, 0, 1, 9),
    ("Rotherbaum", 53.565, 9.985, 5200, 1, 1, 1, 1, 0, 2, 1, 16),
    ("Harvestehude", 53.582, 9.990, 4500, 1, 1, 1, 1, 0, 1, 1, 12),
    ("Eppendorf", 53.592, 9.985, 6200, 1, 1, 1, 1, 0, 1, 2, 18),
    ("Hoheluft-Ost", 53.578, 9.970, 5800, 1, 1, 1, 0, 0, 1, 1, 15),
    ("Lokstedt", 53.598, 9.955, 4800, 1, 1, 1, 1, 0, 0, 1, 14),
    ("Niendorf", 53.618, 9.945, 3200, 1, 1, 1, 1, 0, 0, 1, 12),
    ("Schnelsen", 53.628, 9.915, 2900, 1, 1, 1, 1, 0, 0, 1, 11),
    ("Stellingen", 53.592, 9.925, 4100, 1, 1, 1, 0, 0, 0, 1, 13),
    ("Eimsbüttel", 53.575, 9.955, 5500, 1, 1, 1, 1, 0, 1, 1, 17),
    ("St. Pauli", 53.553, 9.965, 6100, 1, 1, 1, 1, 0, 1, 1, 24),
    ("Sternschanze", 53.563, 9.965, 7200, 1, 1, 1, 0, 0, 1, 1, 20),
    ("Hohenfelde", 53.568, 10.015, 5800, 1, 1, 1, 1, 0, 1, 1, 16),
    ("Uhlenhorst", 53.572, 10.030, 5200, 1, 1, 1, 1, 0, 1, 1, 14),
    ("Barmbek-Süd", 53.582, 10.040, 6400, 1, 1, 1, 0, 0, 1, 2, 19),
    ("Barmbek-Nord", 53.598, 10.040, 5600, 1, 1, 1, 1, 0, 1, 2, 17),
    ("Winterhude", 53.592, 10.000, 4800, 1, 1, 1, 1, 0, 1, 1, 15),
    ("Alsterdorf", 53.608, 10.010, 3900, 1, 1, 1, 1, 0, 0, 1, 12),
    ("Fuhlsbüttel", 53.638, 10.010, 2400, 1, 1, 1, 1, 0, 0, 1, 10),
    ("Langenhorn", 53.648, 10.000, 3100, 1, 1, 1, 1, 0, 0, 1, 11),
    ("Ohlsdorf", 53.618, 10.030, 2800, 1, 1, 1, 1, 0, 0, 1, 9),
    ("Groß Borstel", 53.592, 9.975, 4500, 1, 1, 1, 0, 0, 0, 1, 13),
    ("Dulsberg", 53.578, 10.065, 6800, 1, 1, 1, 0, 0, 1, 1, 18),
    ("Bramfeld", 53.608, 10.085, 4200, 1, 1, 1, 1, 0, 0, 1, 14),
    ("Wandsbek", 53.568, 10.085, 5900, 1, 1, 1, 0, 0, 1, 2, 21),
    ("Marienthal", 53.558, 10.095, 5100, 1, 1, 1, 1, 0, 0, 1, 15),
    ("Jenfeld", 53.578, 10.125, 4800, 1, 1, 1, 1, 0, 0, 1, 16),
    ("Rahlstedt", 53.598, 10.155, 3600, 1, 1, 1, 1, 0, 0, 2, 17),
    ("Farmsen-Berne", 53.618, 10.145, 3200, 1, 1, 1, 1, 0, 0, 1, 13),
    ("Billstedt", 53.538, 10.105, 6200, 1, 1, 1, 0, 0, 1, 1, 19),
    ("Billbrook", 53.538, 10.065, 3800, 1, 1, 1, 0, 0, 1, 1, 14),
    ("Hamm", 53.548, 10.055, 5400, 1, 1, 1, 1, 0, 1, 1, 16),
    ("Horn", 53.558, 10.075, 4900, 1, 1, 1, 1, 0, 0, 1, 12),
    ("Rothenburgsort", 53.528, 10.045, 4100, 1, 1, 1, 1, 0, 1, 1, 15),
    ("Veddel", 53.518, 10.025, 3600, 1, 1, 1, 0, 1, 0, 1, 11),
    ("Wilhelmsburg", 53.508, 10.015, 4400, 1, 1, 1, 1, 1, 0, 1, 18),
    ("Harburg", 53.458, 9.975, 5200, 1, 1, 1, 1, 0, 0, 2, 20),
    ("Heimfeld", 53.468, 9.955, 3800, 1, 1, 1, 1, 0, 0, 1, 14),
    ("Marmstorf", 53.438, 9.965, 2900, 1, 1, 1, 1, 0, 0, 0, 10),
    ("Langenbek", 53.478, 9.945, 3400, 1, 1, 1, 1, 0, 0, 1, 11),
    ("Bergedorf", 53.488, 10.215, 2800, 1, 1, 1, 1, 0, 0, 2, 14),
    ("Lohbrügge", 53.498, 10.195, 3200, 1, 1, 1, 1, 0, 0, 1, 12),
    ("Allermöhe", 53.508, 10.165, 3500, 1, 1, 1, 1, 0, 0, 1, 13),
    ("Volksdorf", 53.648, 10.185, 2100, 1, 1, 1, 1, 0, 0, 0, 9),
    ("Wohldorf-Ohlstedt", 53.688, 10.135, 1200, 1, 1, 0, 1, 0, 0, 0, 6),
    ("Poppenbüttel", 53.658, 10.085, 2400, 1, 1, 1, 1, 0, 0, 1, 10),
    ("Sasel", 53.648, 10.115, 2700, 1, 1, 1, 1, 0, 0, 1, 11),
    ("Duvenstedt", 53.708, 10.095, 1600, 1, 1, 0, 1, 0, 0, 0, 7),
    ("Lemsahl-Mellingstedt", 53.698, 10.115, 1400, 1, 1, 0, 1, 0, 0, 0, 6),
    # Norderstedt (Schleswig-Holstein)
    ("Norderstedt-Mitte", 53.707, 10.003, 3800, 1, 1, 1, 1, 0, 0, 1, 16),
    ("Norderstedt-Garstedt", 53.718, 10.025, 2900, 1, 1, 1, 1, 0, 0, 1, 12),
    ("Norderstedt-Friedrichsgabe", 53.698, 9.985, 2200, 1, 1, 1, 1, 0, 0, 0, 9),
    ("Norderstedt-Hudtwalcker", 53.715, 9.995, 3100, 1, 1, 1, 0, 0, 0, 1, 11),
    ("Norderstedt-Am Waldrand", 53.725, 10.010, 1800, 1, 1, 0, 1, 0, 0, 0, 7),
    # Excluded non-residential (kept for reference but is_residential=0)
    ("Waltershof", 53.523, 9.875, 450, 0, 0, 0, 0, 1, 0, 0, 3),
    ("Finkenwerder", 53.538, 9.845, 380, 0, 0, 0, 0, 1, 0, 0, 2),
    ("Flughafen", 53.630, 10.005, 120, 0, 0, 0, 0, 0, 0, 1, 8),
    ("Kleiner Grasbrook", 53.528, 9.995, 600, 0, 0, 0, 0, 1, 0, 1, 6),
]

HALF_SIZE = 0.012  # ~1.3 km half-width for simplified Stadtteil boxes


def box_polygon(lat, lon, half=HALF_SIZE):
    return [
        [lon - half, lat - half],
        [lon + half, lat - half],
        [lon + half, lat + half],
        [lon - half, lat + half],
        [lon - half, lat - half],
    ]


def main():
    out = Path(__file__).resolve().parent.parent / "data" / "hamburg_stadtteile.geojson"
    features = []
    for row in STADTTEILE:
        (name, lat, lon, density, is_res, school, market, park,
         port_ind, ubahn, sbahn, bus) = row
        features.append({
            "type": "Feature",
            "properties": {
                "stadtteil_name": name,
                "population_density": density,
                "is_residential": is_res,
                "has_school": school,
                "has_supermarket": market,
                "has_park": park,
                "near_port_industrial": port_ind,
                "ubahn_stops": ubahn,
                "sbahn_stops": sbahn,
                "bus_stops": bus,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [box_polygon(lat, lon)],
            },
        })

    geojson = {"type": "FeatureCollection", "features": features}
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    print("Wrote {} Stadtteile -> {}".format(len(features), out))


if __name__ == "__main__":
    main()
