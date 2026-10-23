-- Additive member-2 inbox in the existing A1 core schema.
-- Member 3 may derive typed session/state/analytics tables from this inbox.
CREATE TABLE IF NOT EXISTS core.mqtt_event (
    event_id VARCHAR PRIMARY KEY,
    ev_id BIGINT NOT NULL REFERENCES core.charger_record(ev_id),
    event_type VARCHAR NOT NULL CHECK (event_type IN
        ('price_update', 'session_start', 'meter', 'session_end')),
    event_ts TIMESTAMPTZ NOT NULL,
    session_id VARCHAR,
    connector_id INTEGER,
    topic VARCHAR NOT NULL,
    payload JSON NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS mqtt_charger_time ON core.mqtt_event(ev_id, event_ts);
CREATE INDEX IF NOT EXISTS mqtt_session_time ON core.mqtt_event(session_id, event_ts);
CREATE TABLE IF NOT EXISTS core.mqtt_rejection (
    rejection_id VARCHAR PRIMARY KEY,
    topic VARCHAR NOT NULL,
    payload_base64 VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);
CREATE VIEW IF NOT EXISTS core.mqtt_charger_events AS
SELECT e.*, c.station_name, c.station_address, c.latitude, c.longitude,
       c.charger_type, c.plugs_count, c.operator_id, c.sa4_code
FROM core.mqtt_event e JOIN core.charger_record c USING (ev_id);
