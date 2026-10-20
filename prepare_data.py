"""Adapt all Assignment 1 DC records to the supplied generator."""
import argparse
import csv
import math
from pathlib import Path


def prepare(source, out):
    regions = {"Greater Sydney": "urban", "Rest of NSW": "regional"}
    stations, assumptions, ids = [], [], set()
    with source.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["Charger_Type"] != "DC":
                continue
            sid = row["ev_id"]
            reason = ""
            power = float(row["power_kw"])
            if row["rating_is_compound"].lower() == "true":
                reason = "Mixed ratings: use A1 maximum for each simulated connector"
            elif row["rating_format"] == "single_no_unit":
                reason = "Numeric rating without unit: retain A1 kW interpretation"
            lat, lon = float(row["Latitude"]), float(row["Longitude"])
            plugs = int(row["plugs_count"])
            if not sid or sid in ids:
                raise ValueError(f"Missing or duplicate ev_id: {sid}")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180 and plugs > 0
                    and math.isfinite(power) and power > 0):
                raise ValueError(f"Invalid generator input for ev_id {sid}")
            ids.add(sid)
            name = row["Station_name"].strip()
            if not name or row["station_name_missing"].lower() == "true":
                name = row["Station_address"]
            stations.append(dict(station_id=sid, name=name, lat=lat, lon=lon,
                                 site_type=regions[row["sa4_gcc_name"]],
                                 connectors=plugs, max_kw=power))
            if reason:
                assumptions.append(dict(ev_id=sid, charger_type=row["Charger_Type"],
                                        original_rating=row["Charger_rating"],
                                        max_kw=power, reason=reason))
    if not stations:
        raise ValueError("No DC records found")
    out.mkdir(parents=True, exist_ok=True)
    for name, rows, fields in [
        ("stations_input.csv", stations, list(stations[0])),
        ("assumptions.csv", assumptions, ["ev_id", "charger_type", "original_rating", "max_kw", "reason"]),
    ]:
        with (out / name).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    print(f"{len(stations)} DC records, {sum(s['connectors'] for s in stations)} simulated connectors; {len(assumptions)} assumptions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()
    prepare(args.source, args.out)
