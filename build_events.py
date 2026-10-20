"""Read full generator CSVs; emit only an initial replay window as JSONL."""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ORDER = {"price_update": 0, "meter": 1, "session_end": 2, "session_start": 3}
ZONE = timezone(timedelta(hours=10))


def rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def timestamp(value):
    """Official CSV times are naive; use our fixed +10:00 simulation convention."""
    return datetime.fromisoformat(value).replace(tzinfo=ZONE).isoformat()


def build(data, hours):
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError("hours must be a positive finite number")
    # A small first pass establishes the full generated price interval.
    first, final = None, None
    for row in rows(data / "prices_hourly.csv"):
        ts = row["ts"]
        first = ts if first is None else min(first, ts)
        final = ts if final is None else max(final, ts)
    start = datetime.fromisoformat(first)
    end = start + timedelta(hours=hours)
    if end > datetime.fromisoformat(final) + timedelta(hours=1):
        raise ValueError("Replay window exceeds generated price coverage")
    lower, upper = start.isoformat(), end.isoformat()
    events, last = [], {}
    for row in rows(data / "prices_hourly.csv"):
        if not lower <= row["ts"] < upper:
            continue
        sid, price = row["station_id"], float(row["price_per_kwh"])
        if last.get(sid) != price:
            events.append(dict(type="price_update", ts=timestamp(row["ts"]), station_id=sid,
                               price_per_kwh=price, wholesale_per_kwh=float(row["wholesale_per_kwh"])))
            last[sid] = price
    for row in rows(data / "sessions.csv"):
        common = dict(station_id=row["station_id"], session_id=row["session_id"],
                      connector_id=int(row["connector_id"]))
        if lower <= row["start_ts"] < upper:
            events.append(dict(common, type="session_start", ts=timestamp(row["start_ts"]),
                               vehicle_model=row["vehicle_model"], price_per_kwh=float(row["price_per_kwh"])))
        if lower <= row["end_ts"] < upper:
            totals = {k: float(row[k]) for k in
                      ("charge_minutes", "energy_kwh", "avg_kw", "peak_kw", "price_per_kwh", "cost_aud")}
            events.append(dict(common, type="session_end", ts=timestamp(row["end_ts"]),
                               charge_end_ts=timestamp(row["charge_end_ts"]), **totals))
    for row in rows(data / "session_metering.csv"):
        if row["ts"] >= upper:
            break  # The official generator globally sorts meter events.
        if row["ts"] < lower:
            continue
        values = {k: float(row[k]) for k in
                  ("voltage_v", "current_a", "power_kw", "energy_kwh", "soc_pct")}
        events.append(dict(type="meter", ts=timestamp(row["ts"]), station_id=row["station_id"],
                           session_id=row["session_id"], connector_id=int(row["connector_id"]), **values))
    events.sort(key=lambda e: (e["ts"], ORDER[e["type"]], int(e["station_id"]), e.get("session_id", "")))
    for event in events:
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        event["event_id"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    summary = dict(start=timestamp(lower), end=timestamp(upper), hours=hours,
                   stations=len(last), counts=dict(Counter(e["type"] for e in events)), total=len(events))
    return events, summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--hours", type=float, default=3)
    args = parser.parse_args()
    events, summary = build(args.data, args.hours)
    digest = hashlib.sha256()
    with (args.data / "window.jsonl").open("wb") as f:
        for event in events:
            line = (json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
            f.write(line)
            digest.update(line)
    summary["sha256"] = digest.hexdigest()
    summary["minimum_replay_seconds"] = max(0, len(events) - 1) * 0.1
    (args.data / "window_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
