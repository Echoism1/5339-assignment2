# Member 1 report draft

Merge into the group report. Subscription, storage, dashboard and final system
screenshots must be supplied by the responsible members.

## Introduction

This project extends the Assignment 1 NSW charging-facility data with simulated
pricing and charging activity. Following the updated Assignment 2 FAQ, our
producer uses the complete set of 433 DC records for one day, meeting the revised
target of at least 10,000 hourly prices and approximately 4,000 sessions. Original
`ev_id` identifiers link events to Assignment 1. AC and Upcoming records remain in
the unchanged source snapshot but are outside this simulation. The pipeline
prepares facilities, generates CSV data, constructs a time-ordered event window
and publishes it through MQTT for downstream subscription, DuckDB storage and
mapping. Generation and replay cover different intervals: the complete one-day
data is retained, while every event in the selected window passes through MQTT.

## Synthetic Data Generation

The adapter preserves A1 names, coordinates, parsed power ratings and plug counts;
missing names use addresses. Each original record represents one simulated
facility, and plug counts represent independent simultaneous connectors.
Greater Sydney maps to the generator's urban category and Rest of NSW to regional.
These are modelling assumptions rather than verified operating capacities or site
classifications. Five mixed-rating DC records use A1's maximum of 350 kW uniformly
across connectors; one numeric rating without a unit retains A1's kW
interpretation. A six-row assumption table records the original descriptions and
adopted values without modifying A1.

The official generator runs for 1 September 2026 with seed 5339, NSW1 wholesale
prices, one-minute readings and the FAQ's suggested urban/regional rates of 3/2
sessions per connector per day. Its original charging model and random process
are retained. Our only generator modification checks that each required hour has
twelve distinct, aligned, finite five-minute AEMO observations, preventing silent
fallback for incomplete coverage. The resulting 10,392 hourly prices, 3,906
sessions and 118,030 meter readings meet the revised data-volume target. Thirty
sessions continue past midnight and retain their complete readings and endings.
Six facilities have no session in the stochastic one-day run; their prices and
metadata are still included.

These are hypothetical retail tariffs and battery-side readings, not observed
operator prices or measured grid telemetry. The supplied vehicle limits,
derating, taper and four-hour charging cap remain simplifications. Each session
is billed at its starting price. Generation took 2.04 seconds and approximately
94 MiB peak memory on the test machine. Repeating the complete generation with
the same inputs and seed produced identical hashes for all four CSV outputs.

## MQTT Publishing

The replay covers 1 September 00:00–03:00 as a half-open interval with a fixed
UTC+10 timestamp convention. Starting at the dataset origin avoids reconstructing
prior active sessions. It contains 2,452 messages: 433 initial prices, 71 session
starts, 1,886 meter readings and 62 session ends. Prices do not change inside this
window, so repeated hourly values are suppressed. Session activity supports
changing occupancy and live progress curves. Nine sessions remain active at the
boundary; no artificial endings or outside-window events are sent.

Messages contain a type, timestamp, A1 facility identifier and deterministic
SHA-256 event ID. Session events also identify the session and connector. Numeric
fields retain the generator's names and units. Start messages exclude future
outcomes; meter energy is cumulative; end events represent unplugging, which can
follow charging completion. Events sort globally by time, then prices, meters,
ends and starts, followed by numeric facility ID and session ID. Initial prices
are always included, and later price events represent actual changes, including
returns to an earlier value.

A single group-specific MQTT topic carries all four types with QoS 1 and no
retention. The publisher waits for each broker acknowledgement, then at least
0.1 seconds before the next message. Failure stops the run and reports the last
confirmed ID. Replays preserve IDs so downstream consumers can deduplicate
redelivery. The minimum replay duration is 245.1 seconds, plus overhead.
Full-data validation confirms source associations, connector occupancy,
cumulative energy and the complete window contents. The local subscriber received
all 2,452 messages with identical content and order; the minimum measured send
interval was 0.100723 seconds. Evidence is recorded in `verification/mqtt.json`.
Final group acceptance must
compare expected, received and stored IDs and payloads; broker acknowledgements
alone cannot establish successful DuckDB storage. Historical dashboard metrics
must describe received events rather than the unseen remainder of the day.

## Integration and disclosure notes (outside the main report text)

- Subscriber-side DuckDB ingestion and the dashboard require joint verification;
  do not present them as tested on the basis of the producer's local MQTT result.
- The group submission must include its subscriber-side DuckDB database, as
  required by FAQ Q6. This member 1 package does not contain that database.
- Contribution statements must describe actual student contributions.
- GenAI disclosure: OpenAI Codex assisted with planning, Python implementation,
  validation, local execution, dataset reduction and this draft. Review the code,
  assumptions and results, and submit the required usage report in the course's
  prescribed format.
