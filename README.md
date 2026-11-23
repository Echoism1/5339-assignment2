# EV Charging Streaming and Analysis

Python 3.12 application for sending simulated charging events through MQTT,
storing them in DuckDB, and displaying station activity on a Dash map.
Run commands from this folder.

## Install and run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py --mode saved
```

On Windows, create the environment with `python -m venv .venv` and activate it
with `.venv\Scripts\Activate.ps1`. Open <http://127.0.0.1:8050>.
Saved mode uses `artifacts/dashboard.json`. The map background needs Internet access.

For live MQTT replay:

```bash
python -m pip install -r requirements-test.txt
python tools/run_dashboard_demo.py --port 8050 --mqtt-port 18883
```

The launcher starts the broker, subscriber, dashboard and publisher. It uses a
new database copy under `runtime/demo-runs/`. Replay starts after eight seconds
and takes about five minutes. Use `--publish-delay 30` for a longer initial wait.
Ctrl+C stops the services. Run paths are saved in `runtime/latest_demo.json`.
The subscriber finishes queued writes before closing its database.

Station 306 starts and finishes a session during replay. Station 524 has a
charging curve that grows as readings arrive. Final totals are 2,452 events,
62 completed sessions, 9 active sessions and 1,674.91 kWh from completed sessions.

## Run components separately

Start one broker:

```bash
docker compose up -d
```

Alternatively, install `requirements-test.txt` and run
`python tools/dev_broker.py --port 1883`. The Python broker stores its state in
memory; Mosquitto in Docker saves its state to disk.

Run these commands in separate terminals, in order. Wait for the subscriber's
`READY` log before starting the publisher.

```bash
python subscriber.py listen --host localhost --port 1883 --topic comp5339/TUT19-AssignmentGroup-08/events --snapshot runtime/dashboard.json
python app.py --mode live --snapshot runtime/dashboard.json
python publisher.py data/window.jsonl --host localhost --port 1883 --topic comp5339/TUT19-AssignmentGroup-08/events
```

Publisher and subscriber must use the same host, port and topic. The included
database already contains these events, so replaying into it skips duplicates.
Use the demo launcher for a fresh replay, or copy a closed original A1 database:

```bash
python subscriber.py init --a1-db /path/to/a1.duckdb --db runtime/fresh.duckdb
python subscriber.py listen --db runtime/fresh.duckdb --snapshot runtime/dashboard.json
```

The target database must not already exist. Stop the subscriber before opening
its database from another process. Dashboard modes are `saved`, `live` and
`auto`; `auto` uses saved data until live data arrives.

For shutdown from another script, start the subscriber with
`--stop-file runtime/subscriber.stop`, then create that file to stop it.
Choose a file that does not already exist. The demo launcher manages this file
automatically and waits for the subscriber to finish.

## Files

| Path | Purpose |
|---|---|
| `prepare_data.py`, `build_events.py`, `publisher.py` | Prepare stations, build events and publish messages |
| `COMP5339_2026s2_Assignment2_data _generator/` | Data generator |
| `subscriber.py`, `ingestion/` | Message checks and database writes |
| `analytics/`, `sql/` | Session summaries, queries, charts and table definitions |
| `app.py`, `dashboard/`, `assets/` | Map, station details and live charts |
| `data/a1_chargers.csv` | A1 station data |
| `data/stations_input.csv`, `data/assumptions.csv` | Generator input and power assumptions |
| `data/stations.csv`, `data/prices_hourly.csv`, `data/sessions.csv`, `data/session_metering.csv` | Generated data |
| `data/aemo_cache/` | AEMO prices for offline generation |
| `data/window.jsonl`, `data/window_summary.json` | Replay messages, counts and checksum |
| `database/assignment2.duckdb` | A1 data, received messages and analysis tables |
| `artifacts/dashboard.json` | Saved dashboard data |
| `tests/`, `tools/` | Tests, demo launcher and checks |

`runtime/` and `verification/` hold generated output and are ignored by Git.
`sql/ddl.sql` defines all event tables, analysis tables, indexes and views added
to the A1 database. The subscriber runs it automatically before rebuilding
analysis data. It can also be executed directly against a closed A1 database
copy; it requires the existing A1 tables.
`sql/a1_schema_reference.sql` describes the original A1 tables; normal runs use
the included database and do not execute this file.

## Rebuild the data

These commands overwrite the generated CSVs and replay files:

```bash
python prepare_data.py data/a1_chargers.csv
python "COMP5339_2026s2_Assignment2_data _generator/generate_dataset.py" --stations data/stations_input.csv --start 2026-09-01 --days 1 --seed 5339 --sessions-per-connector-day 3 2 --out data
python build_events.py --hours 3
```

The dataset contains 433 DC stations, 1,658 simulated connectors, 10,392 hourly
prices, 3,906 sessions and 118,030 meter readings. Station IDs match A1 `ev_id`.
Greater Sydney is `urban`; Rest of NSW is `regional`. Five mixed power ratings
use the A1 maximum per connector; one unitless rating uses the A1 kW value.
These six cases are listed in `data/assumptions.csv`.

The replay covers 00:00–03:00 on 1 September 2026, with an exclusive end time.
CSV times have no time zone; JSON times use UTC+10. The event list contains
433 price updates, 71 session starts, 1,886 readings and 62 session ends.
Events are ordered by time, then type (price, meter, end, start), station and
session. Retail prices are sent initially and when they change. Rounded retail
prices stay constant in this window even though wholesale prices vary.

The generator checks for twelve valid five-minute prices per hour. For a
different dataset, use a fresh subscriber database and rebuild the saved
snapshot. Demo checks use the station IDs and counts of the included dataset.

## Storage and metrics

Messages use MQTT QoS 1 with at least 0.1 seconds between sends. Each event has
a SHA-256 ID based on its JSON content. The subscriber saves valid messages in
`core.mqtt_event` and invalid messages with their rejection reason in
`core.mqtt_rejection`. Repeated event IDs are skipped. One worker writes to
DuckDB and confirms receipt after saving. It stops if its queue fills up;
unsaved messages remain unconfirmed and can be replayed.

`core.meter_reading`, `core.charging_session` and `core.current_price` hold
readings, session summaries and latest prices. Each event and its table updates
are saved in one transaction. Startup rebuilds these tables from saved messages.
Latest values use event time, so arrival order does not determine the result.
Typed columns and saved session summaries reduce repeated JSON processing.

`analytics.station_metrics` joins A1 station details with current and completed
activity. A connector stays occupied until unplugging. Utilisation is occupied
connector time divided by available connector time over the received event
period; it assumes complete receipt from the simulation start.
`analytics.daily_usage` groups completed sessions by their finish date in Sydney.
Energy totals use final session values, since adding cumulative meter readings
would double-count energy. Sessions still active at the replay boundary remain active.

Station details show usage type, membership requirements and the operator's
website when available in A1. Operator phone numbers and email addresses appear
in the side panel. Missing optional details are omitted.

The dashboard reads JSON snapshots written by the subscriber. Each file is
replaced only after the new content is complete. Snapshot update time and
simulation event time are separate: a recent snapshot does not confirm an
active MQTT connection.
On Windows, replacement retries briefly if a reader holds the file. If a
snapshot cannot be written, the subscriber logs a warning and keeps saving
messages. It tries again at the next update; the dashboard keeps its last
valid data and shows when it becomes stale.

## Tests

```bash
python -m unittest discover -s tests -v
python tools/verify_ingestion.py
python tools/benchmark_member3.py
```

Tests use temporary copies of the included data and cover file-lock failures,
queued writes during shutdown, and process cleanup. The ingestion check
compares saved messages with `data/window.jsonl`; stop the subscriber first.
The benchmark compares query results and timings.

With `requirements-test.txt` installed, run a full MQTT check:

```bash
python tools/verify_member3_mqtt.py
```

The MQTT check verifies saved events, A1 table preservation, snapshot updates,
session completion and charging-curve growth. Unit tests check the popup contents.
To check a demo database after stopping it, replace `RUN_ID` with its run folder:

```bash
python tools/verify_ingestion.py --db runtime/demo-runs/RUN_ID/demo.duckdb
```

Additional checks accept a closed original A1 database:

```bash
python tools/verify_a1_preserved.py --a1-db /path/to/a1.duckdb
python tools/test_mqtt_recovery.py --a1-db /path/to/a1.duckdb
```
