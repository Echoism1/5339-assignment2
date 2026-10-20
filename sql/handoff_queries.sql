-- Read-only examples for member 3. Stop the separate subscriber process first.
-- These queries illustrate the inbox contract; they do not implement a live API.

-- 1. All simulated DC stations and their latest received price (433 rows).
-- A station without a received price remains NULL rather than an invented zero.
WITH prices AS (
    SELECT ev_id, event_ts,
           CAST(json_extract_string(payload, '$.price_per_kwh') AS DOUBLE) AS price_aud_per_kwh,
           row_number() OVER (PARTITION BY ev_id ORDER BY event_ts DESC, event_id DESC) AS rn
    FROM core.mqtt_event WHERE event_type = 'price_update'
)
SELECT c.ev_id, c.station_name, c.station_address, c.latitude, c.longitude,
       c.plugs_count, p.price_aud_per_kwh, p.event_ts AS price_event_ts
FROM core.charger_record c
LEFT JOIN prices p ON p.ev_id = c.ev_id AND p.rn = 1
WHERE c.charger_type = 'DC'
ORDER BY c.ev_id;

-- 2. Sessions whose latest received lifecycle event is a start (9 rows).
-- This window begins at dataset origin; an arbitrary mid-session window would
-- need separate handling for missing starts and unknown initial occupancy.
WITH lifecycle AS (
    SELECT ev_id, session_id, connector_id, event_type, event_ts,
           row_number() OVER (
               PARTITION BY ev_id, session_id
               ORDER BY event_ts DESC,
                        CASE WHEN event_type = 'session_end' THEN 1 ELSE 0 END DESC,
                        event_id DESC
           ) AS rn
    FROM core.mqtt_event WHERE event_type IN ('session_start', 'session_end')
)
SELECT ev_id, session_id, connector_id, event_ts AS started_at
FROM lifecycle WHERE rn = 1 AND event_type = 'session_start'
ORDER BY ev_id, session_id;

-- 3. A historical metric: completed sessions in received data (sum = 62).
-- Stations with no completed sessions are retained. This is not a full-day count.
SELECT c.ev_id, count(DISTINCT e.session_id) AS completed_sessions_in_received_window
FROM core.charger_record c
LEFT JOIN core.mqtt_event e ON e.ev_id = c.ev_id AND e.event_type = 'session_end'
WHERE c.charger_type = 'DC'
GROUP BY c.ev_id ORDER BY c.ev_id;

-- 4. Meter points for a curve (1,886 rows across all sessions).
-- In the service, parameterise WHERE ev_id=? AND session_id=? to select one
-- session. Do not concatenate user inputs into SQL or sum cumulative energy.
SELECT ev_id, session_id, connector_id, event_ts,
       CAST(json_extract_string(payload, '$.power_kw') AS DOUBLE) AS power_kw,
       CAST(json_extract_string(payload, '$.energy_kwh') AS DOUBLE) AS cumulative_energy_kwh,
       CAST(json_extract_string(payload, '$.soc_pct') AS DOUBLE) AS soc_pct
FROM core.mqtt_event WHERE event_type = 'meter'
ORDER BY ev_id, session_id, event_ts, event_id;
