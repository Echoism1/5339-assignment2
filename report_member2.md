# Member 2 report draft

Merge and shorten to fit the group's six-page content limit. This section covers
subscription and ingestion, not the completed dashboard or final analytical schema.

## MQTT Subscription and Event Processing

The gateway subscribes to the agreed group event topic at QoS 1 using Paho MQTT.
The producer starts only after the subscriber receives a successful subscription
acknowledgement. A stable client identifier and persistent MQTT session support
reconnection, with a one-to-thirty-second exponential backoff and resubscription
after each connection. Since QoS 1 permits redelivery, the deterministic producer
event ID is also the database primary key.

The network callback places messages into a bounded queue. A single worker owns
the DuckDB write connection, separating network reception from storage. Each
message is validated as UTF-8 JSON against the producer's four event schemas;
checks include required fields, strict numeric types, finite values, timezone-aware
timestamps, connector bounds and the canonical SHA-256 identity. Wholesale prices
may be negative. Malformed messages are durably quarantined with their original
bytes, topic and rejection reason. Manual MQTT acknowledgement follows either a
successful event commit or a successful quarantine commit. A writer failure or
queue overflow stops processing without acknowledging uncommitted deliveries.
Recovery can replay the original window safely because committed IDs are deduplicated.

For integration, we copy the team's actual Assignment 1 DuckDB rather than build
an unrelated station table. Generator station IDs are checked against the original
DC records, connector counts and coordinates. The additive `core.mqtt_event` table
uses a foreign key to `core.charger_record.ev_id` in the same database and schema,
preserving the existing station, operator, region, spatial and augmentation data.
The full JSON payload, event time and reception time remain available for session
analytics and live meter curves. A joined view exposes static station metadata;
charger/time and session/time indexes provide initial lookup support. Final
stateful analytics and physical-design evaluation belong to the storage component.

Validation replays the complete 2,452-message window through a local MQTT broker
using the publisher's unchanged event and acknowledgement logic and its minimum
0.1-second delay (the measurement clock was updated as documented). The read-only
verifier compares every stored event ID and decoded payload with the source,
checks arrival order, type counts and A1 associations, and counts active sessions.
Separate broker tests cover malformed and retained-message quarantine, wrong-topic
isolation, duplicate delivery, broker restart with resubscription and subscriber
restart with persistent deduplication. The current executable evidence is in
`verification/task2/`; the group's dashboard behaviour requires its own joint test.

## Challenges and Limitations

Broker acknowledgement does not establish a database commit, so consumption is
verified independently. The replay begins at the dataset origin and ends with nine
sessions still active; no endings are fabricated. The three-hour window contains
initial tariffs but no subsequent price change, so charging activity must provide
the visible dynamics. Historical measures must describe received events rather
than claim coverage of the full generated day. A separate dashboard process cannot
read a DuckDB file held by another process for writing; the group must integrate
queries into a shared service or publish read snapshots. The local Python test
broker has no on-disk session persistence; replay remains the recovery fallback.
The persistent Mosquitto configuration was also exercised on Docker with all four
event types; the team's final combined deployment still needs a dashboard test.

## Contribution and genAI disclosure notes

Do not submit an invented contribution statement. After personally reviewing and
testing the implementation, describe the work you actually performed, within the
course's 100-word requirement. OpenAI Codex assisted with requirement interpretation,
subscriber code, schema additions, tests, local execution, review and this draft.
Record that assistance in the course's required genAI report and explain your own
validation and changes. Final grades depend on the submitted work and your ability
to explain it.
