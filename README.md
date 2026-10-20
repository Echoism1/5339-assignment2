# COMP5339 Assignment 2 — Member 1

Tasks 1–2: data generation and MQTT publishing. Following the updated tutor FAQ
(Q1–3), the submission uses **all 433 DC records for one day**, generating
**10,392 hourly prices and 3,906 charging sessions**. The FAQ replaces the earlier
14-day requirement with at least 10,000 hourly prices and approximately 4,000
sessions (3,900 is acceptable). The complete one-day CSV outputs are included;
MQTT replays every event in an initial three-hour window.

## Files and submission scope

| Location | Purpose |
|---|---|
| `prepare_data.py` | Extract all A1 DC records and record power assumptions |
| `COMP5339_2026s2_Assignment2_data _generator/` | Official generator, with an AEMO coverage check |
| `build_events.py` | Construct ordered window events from the CSVs |
| `publisher.py` | Publish JSONL messages through MQTT |
| `data/a1_chargers.csv` | Unmodified A1 snapshot, including AC and Upcoming records |
| `data/stations_input.csv`, `data/assumptions.csv` | Generator input and six power assumptions |
| `data/stations.csv` | Generated facilities, parameters and base prices |
| `data/prices_hourly.csv` | All one-day hourly prices |
| `data/sessions.csv`, `data/session_metering.csv` | All sessions and minute readings, including tails after midnight |
| `data/aemo_cache/` | Original AEMO monthly wholesale-price CSV |
| `data/window.jsonl`, `data/window_summary.json` | Actual MQTT message list and its counts, boundaries and checksum |
| `verification/` | Checks and logs for this DC/day dataset |
| `report_member1.md` | Draft sections for the group report |

`member1_submission.zip` contains the member 1 handoff. It excludes the local
`backups/ac_dc_14days/` archive, virtual environment, Python caches and original
assignment ZIP/PDF. The backup retains the previous large dataset, its scripts,
documentation and verification; its results do not describe the new submission.
The group must still supply the subscriber-side DuckDB database and dashboard.

For GitHub collaboration, the source files, current `data/` and `verification/`
are tracked directly. ZIP files and `backups/` remain local and are not uploaded.
After cloning, follow the setup commands below; the included dataset is ready to
publish without regeneration. Each member should work on a separate branch and
merge changes through a pull request.

## Setup and reproduction

Tested with Python 3.12. Data preparation, generation and event construction use
the standard library; `paho-mqtt` is the only additional runtime dependency.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python prepare_data.py data/a1_chargers.csv
python "COMP5339_2026s2_Assignment2_data _generator/generate_dataset.py" --stations data/stations_input.csv --start 2026-09-01 --days 1 --seed 5339 --sessions-per-connector-day 3 2 --out data
python build_events.py --hours 3
```

These commands overwrite generated outputs. The included AEMO cache supports
offline regeneration after dependency setup; it is downloaded only if absent.
Use dynamic prices and one-minute readings. Generation took 2.04 seconds and
approximately 94 MiB peak resident memory on this machine; window construction
took 0.18 seconds. Timings vary by machine.

Start the agreed broker and subscriber before publishing:

```bash
python publisher.py data/window.jsonl --host localhost --port 1883 --topic comp5339/YOUR_GROUP/events
```

## Dataset and modelling assumptions

Generation covers **[2026-09-01 00:00, 2026-09-02 00:00)**, seed 5339, NSW1.
The urban/regional session-rate inputs are 3 and 2 per connector per day, as
suggested by the FAQ. The realised count also depends on demand, price and
connector occupancy. Other simulation parameters retain official defaults.

| Output | Count |
|---|---:|
| DC records (urban / regional) | 433 (252 / 181) |
| Simulated connectors | 1,658 |
| Hourly prices (24 per record) | 10,392 |
| Charging sessions | 3,906 |
| Meter readings | 118,030 |
| Sessions ending after midnight | 30 |

- `station_id = str(ev_id)` preserves A1 identity. Each source record is one
  simulated facility; records sharing coordinates are not merged. All 433 DC
  records are included. The 1,427 AC and 98 Upcoming records remain in the A1
  snapshot but receive no synthetic sessions or prices in this submission.
- Names use A1 names, with the address as fallback. Coordinates retain A1 latitude
  and longitude and its WGS84 assumption. `plugs_count` represents simulated
  simultaneous connectors, not verified shared-power limits or physical bays.
- A1 `power_kw` values are retained, as permitted by FAQ Q4. Five mixed DC ratings
  use A1's 350 kW maximum for every simulated connector; one unitless numeric
  rating retains A1's kW interpretation. `assumptions.csv` lists these six records.
  No power values are imputed, and the A1 source is unchanged.
- Greater Sydney maps to `urban`, Rest of NSW to `regional`; these are simulation
  categories. The original vehicle mix, derating, taper, battery-side voltage and
  current, and four-hour charging cap are unchanged. Telemetry is simulated,
  not measured grid-side data.
- Retail prices are hypothetical tariffs driven by AEMO wholesale deviations.
  Sessions use the price at their start; idle time is not charged. The only
  generator modification checks twelve distinct, aligned, finite five-minute
  AEMO observations per required hour, rejecting missing or invalid coverage.
  See `verification/generator.patch`; the random process is unchanged.
- Complete endings and readings are retained for all 30 cross-midnight sessions.
  Six facilities (IDs 124, 270, 352, 921, 959 and 969) have no session in this
  stochastic one-day run. They retain all 24 prices and their metadata; no
  sessions are fabricated to fill gaps.

## Replay window

The window is **[2026-09-01T00:00:00+10:00, 2026-09-01T03:00:00+10:00)**.
The CSVs retain naive times; JSON uses a fixed UTC+10 convention for September.
Only events inside the half-open window are sent.

| Event type | Count |
|---|---:|
| `price_update` | 433 |
| `session_start` | 71 |
| `meter` | 1,886 |
| `session_end` | 62 |
| **Total** | **2,452** |

All 433 facilities have an initial price. Retail prices stay unchanged throughout
this window, so repeated hourly prices are correctly omitted. Charging starts,
ends and meter readings provide dynamic occupancy and live session progress.
Nine sessions remain active at 03:00; do not fabricate their endings.

The minimum sending time is **245.1 seconds (about 4.1 minutes)**, plus connection,
acknowledgement and processing overhead. No window events are sampled or dropped.
`--hours` adjusts the end while always starting at the dataset origin. If a price
change demonstration is needed, a six-hour window includes the change at 05:00;
rebuild and publish its complete event list, then refresh verification for that
window. Arbitrary mid-period starts are not supported because they require
reconstructing earlier prices and active sessions.

## JSON and MQTT contract

One agreed topic carries all event types: `comp5339/<group>/events`. Messages are
UTF-8 JSON objects, QoS 1, `retain=False`.

| Fields | Meaning |
|---|---|
| `event_id` | Lowercase SHA-256 of canonical event JSON before adding the ID |
| `type` | One of the four types below |
| `ts` | Event time as ISO 8601 with `+10:00` |
| `station_id` | String representation of A1 `ev_id` |
| `session_id`, `connector_id` | Session identity and integer connector number; absent on price updates |

Canonical JSON uses sorted keys, UTF-8, unescaped Unicode and separators `,` and
`:` without spaces; non-finite numbers are forbidden. Replaying the same event
preserves its ID. A changed event payload has a different ID.

| Type | Additional fields |
|---|---|
| `price_update` | `price_per_kwh`, `wholesale_per_kwh` |
| `session_start` | `vehicle_model`, `price_per_kwh` |
| `meter` | `voltage_v`, `current_a`, `power_kw`, `energy_kwh`, `soc_pct` |
| `session_end` | `charge_end_ts`, `charge_minutes`, `energy_kwh`, `avg_kw`, `peak_kw`, `price_per_kwh`, `cost_aud` |

Numbers are JSON numbers; prices use AUD/kWh, cost AUD, power kW, energy kWh,
voltage V, current A and SoC percent. Meter `energy_kwh` is cumulative within a
session: **do not sum readings**. Final readings have three-decimal energy while
session totals have two decimals, so compare with the source rounding tolerance.

Start messages contain no future totals or ending times. A `session_end` occurs
at unplugging (`end_ts` in the source); `charge_end_ts` can be earlier, while the
connector remains occupied. Per-minute readings stop at charging completion.

Events sort by time, then `price_update`, `meter`, `session_end`, `session_start`,
then numeric station ID and session ID. This places a final reading before an
end and releases a connector before a simultaneous new start. The first price is
always sent; later prices are suppressed only when unchanged from the previous
published price at that facility. A return to an older price is a new change.

The publisher awaits each broker acknowledgement, then sleeps at least 0.1 seconds.
It stops on connection/publish failure or a 10-second acknowledgement timeout,
reporting the last confirmed event ID. It has no custom retry or resume mechanism.
After a failure, restart the window and let the consumer deduplicate by `event_id`.
The subscriber must be ready before replay: retained messages are not a history.

## Verification and team handoff

The new dataset was checked against the untouched A1 source: IDs, coordinates,
power, connectors, assumptions, all 24 prices per facility, session associations,
connector occupancy, minute-reading coverage, cumulative energy and rounded final
energy. All 2,452 window payloads, ordering and IDs match their source CSVs. A
second complete generation produced identical hashes for all four output CSVs.
`verification/data_checks.json` records counts, parameters and data checksums.

The local subscriber received all 2,452 messages with identical content and order.
The minimum measured send interval was 0.100723 seconds, and connection refusal
correctly stopped publishing. Results are recorded in `verification/mqtt.json`
and the publisher log. Validation utilities ran temporarily outside the project; no test files are
required to prepare or publish data. Archived tests and MQTT results under
`backups/` apply only to the old dataset.

For group integration:

1. Member 2 starts the agreed broker and subscriber before replay. QoS 1 can
   redeliver, so members 2–3 deduplicate by `event_id`.
2. Compare the expected IDs from `window.jsonl`, unique received IDs and stored
   DuckDB IDs, together with event counts and payloads. Generated events must
   enter the database through MQTT, not a direct CSV/JSONL bulk load.
3. Member 4 shows all 433 simulated DC records, including no-session facilities,
   joined to A1 by `ev_id`. If the map also shows AC or Upcoming records, label
   them as outside the simulation; do not invent dynamic state for them.
4. Historical metrics use received events only. They cannot claim coverage of
   the full generated day unless those events have actually been received.

A broker acknowledgement does not prove a DuckDB commit. Members 2–4's code is
not included here, so subscriber-to-DuckDB integration and map behaviour still
require joint verification. FAQ Q6 also requires the group's subscriber-side
DuckDB database in the final submission. Review and merge the report draft and
complete the required truthful contribution and genAI usage statements.


---

## Member 2 subscription delivery

This section adds MQTT subscription and ingestion to the producer described above.
It covers member 2's group allocation (Stream Gateway and Ingestion), which is
part of official assignment Task 3. The earlier setup and handoff notes describe
member 1's producer package; use the instructions below to run the subscriber.
The repository now includes a subscriber-side database, while the final session
analytics and dashboard still require integration by members 3 and 4.

### Requirements and setup

**Python 3.12 is recommended.** The implementation was verified with Python
3.12.14, paho-mqtt 2.1.0 and DuckDB 1.5.6. Runtime dependencies are pinned in
`requirements.txt`; no particular environment manager or environment name is
required.

Run all commands from the repository root. Use an existing Python 3.12 virtual
environment, or create one:

```sh
python -m venv .venv
```

Activate it using the command for your shell:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```sh
# macOS / Linux
source .venv/bin/activate
```

Then install the runtime dependencies:

```sh
python -m pip install -r requirements.txt
```

The included dataset is ready to replay. Regenerating the producer CSVs or
rebuilding `data/window.jsonl` is not required for these instructions.

### Added files

| Location | Purpose |
|---|---|
| `subscriber.py` | init/listen CLI, MQTT callbacks, bounded queue and single database writer |
| `ingestion/` | Event validation, A1 identity checks, durable deduplication and quarantine |
| `sql/ingestion.sql` | Additive event/rejection tables and joined view in A1's `core` schema |
| `sql/a1_schema_reference.sql` | Original A1 DDL reference; rebuilding it requires the spatial extension |
| `database/assignment2.duckdb` | A1 database extended with the 2,452 events already received through MQTT |
| `tools/dev_broker.py` | Optional Python MQTT broker for local testing and demonstrations |
| `compose.yaml`, `broker/mosquitto.conf` | Alternative local Mosquitto setup using Docker |
| `tests/`, `tools/test_mqtt_recovery.py` | Contract, persistence and isolated MQTT recovery tests |
| `tools/review_member1.py` | Producer handoff inventory and replay reconstruction check |
| `tools/verify_ingestion.py` | Read-only source-to-database verification |
| `tools/verify_a1_preserved.py` | Exact comparison of original A1 tables with the extended database |
| `verification/task2/` | Recorded test results; temporary scratch databases are excluded from Git |
| `docs/TASK2_GUIDE_ZH.md`, `docs/TASK2_REVIEW.md` | Detailed handoff guide and review record |
| `report_member2.md` | Draft material to merge into the group report, not runtime instructions |

### Step 1 Start or choose one MQTT broker

A broker is required, but **Docker is optional**. Choose one of the following
options; do not start multiple brokers on the same host and port.

**Option A: Python broker for a local demonstration**

Install the optional test/demo dependencies, then start the included broker:

```sh
python -m pip install -r requirements-test.txt
python tools/dev_broker.py
```

Keep this terminal running. The broker listens on `127.0.0.1:1883`. It does not
persist sessions or offline messages to disk, so restart the replay if its state
is lost. `requirements-test.txt` includes amqtt 0.12.1 and is not required when
using another broker.

**Option B: An existing local or team MQTT broker**

Use a running Mosquitto or other compatible MQTT broker that accepts this
project's plain MQTT connection and QoS 1 subscription. If you choose local
Mosquitto, install and start it separately. For a broker on another machine, pass
its address using `--host` in both subscriber and publisher commands below. The
subscriber does not currently expose username/password or TLS options.

**Option C: Mosquitto using Docker**

If Docker Desktop or Docker Engine with Compose is installed and running:

```sh
docker compose up -d
```

This publishes the broker on `127.0.0.1:1883` and stores broker state in a Docker
volume. The supplied configuration is for local use; it does not expose a broker
for other computers to connect to. To stop it after use:

```sh
docker compose stop mqtt
```

### Step 2 Start the subscriber

Open a second terminal, activate the same Python environment and run from the
repository root:

```sh
python subscriber.py listen --host localhost --port 1883 --topic comp5339/TUT19-AssignmentGroup-08/events
```

**Wait for `READY` before publishing.** This confirms the broker granted the QoS 1
subscription. Messages published before the first subscription are not recovered
from the non-retained stream.

The default database is `database/assignment2.duckdb`. It is already initialised
and contains the verified replay. You do not need the original A1 database merely
to run the subscriber against this included file.

### Step 3 Start the publisher

Open a third terminal with the environment activated and run:

```sh
python publisher.py data/window.jsonl --host localhost --port 1883 --topic comp5339/TUT19-AssignmentGroup-08/events
```

Use the same host, port and exact topic in both commands. The example uses the
team topic; replace it consistently if your team agrees on another topic. Run only
one subscriber with a given client ID; separate subscriber instances require
separate `--client-id` values and must not write the same DuckDB file from
separate processes.

The full 2,452-message replay takes at least about 4.1 minutes plus overhead.
The three-hour event window has initial prices but no subsequent tariff changes;
charging-session events and meter readings provide the changing activity.

**Replaying into the included database produces duplicates rather than new rows.**
This is expected: `event_id` deduplication preserves one row per event. For a
fresh ingestion demonstration, use a new database as described below.

After publishing completes, stop the subscriber with Ctrl+C. It finishes queued
work before exiting. Then verify the database:

```sh
python tools/verify_ingestion.py
```

For the supplied window, verification should report 2,452 events, 433 chargers
with received prices, nine active sessions at the window boundary, zero rejected
source events and `passed: true`. The included completed database can pass this
check even before another replay; use a fresh database to demonstrate new inserts.

### Optional Start with an empty event inbox

This requires the team's **original A1 DuckDB database**, not a CSV export or the
already populated A2 database. Close/checkpoint the original before copying it.
Replace `path/to/a1/ev_chargers.duckdb` below with its actual location:

```sh
python subscriber.py init --a1-db "path/to/a1/ev_chargers.duckdb" --db database/fresh_demo.duckdb
python subscriber.py listen --db database/fresh_demo.duckdb --host localhost --port 1883 --topic comp5339/TUT19-AssignmentGroup-08/events
```

`init` copies the original A1 database and adds the ingestion objects; it refuses
to overwrite an existing target. Start the broker first, wait for subscriber
`READY`, then run the publisher from Step 3. After publishing and stopping the
subscriber, verify the selected database explicitly:

```sh
python tools/verify_ingestion.py --db database/fresh_demo.duckdb --output verification/task2/fresh_demo_verification.json
```

A1 spatial queries and rebuilding the original DDL require the spatial extension
for the installed DuckDB version. Ingestion itself reads A1 scalar identifiers,
connector counts and coordinates.

### Optional Tests and recorded evidence

The basic contract tests can run with the runtime dependencies:

```sh
python -m unittest discover -s tests -v
```

The persistence test is skipped unless `A1_TEST_DB` points to an original A1
database. To include it, set that variable for your shell; substitute the actual
path in these examples:

```powershell
# Windows PowerShell
$env:A1_TEST_DB="path/to/a1/ev_chargers.duckdb"
```

```sh
# macOS / Linux
export A1_TEST_DB="path/to/a1/ev_chargers.duckdb"
```

Then rerun the unit tests. The isolated broker-recovery tests additionally need
`requirements-test.txt` and a free local port 18884:

```sh
python -m pip install -r requirements-test.txt
python tools/test_mqtt_recovery.py --a1-db "path/to/a1/ev_chargers.duckdb"
python tools/review_member1.py
```

Recorded evidence in `verification/task2/` covers full replay, payload equality,
A1 preservation, duplicates, malformed-message quarantine, wrong-topic isolation,
reconnection, process restart and recovery after queue overflow. Separate
Mosquitto tests cover all four event types and offline delivery after a broker
restart. These are recorded results, not a claim that the final dashboard has
been tested.

### Integration notes

The subscriber commits valid events to `core.mqtt_event` or saves invalid messages
to `core.mqtt_rejection` before acknowledging delivery. A foreign key links each
valid event to A1's `core.charger_record.ev_id`; QoS 1 redelivery is deduplicated
by `event_id`. Broker ACKs alone do not prove a database commit.

Stop the subscriber before opening its DuckDB file from another process. A live
dashboard needs an agreed shared-service or read-snapshot design; it cannot simply
open the file while a separate subscriber process holds its write lock. Members
3 and 4 still own session state, historical analytics, final query optimisation,
the map and live session curves.

The producer's only code adjustment is its interval measurement clock, from
`monotonic()` to `perf_counter()`, to avoid coarse timing measurements on
Windows/Python 3.12. Event contents, publish ACK handling and the minimum
0.1-second sleep are unchanged. The complete replay was repeated after this
adjustment.
