-- ==============================================================================
-- USYD CODE CITATION ACKNOWLEDGEMENT
-- I declare that the following assistance was received in this file:
-- - Generative AI assistance: Anthropic Claude 5.5 Opus was consulted in a limited
--   assistive capacity for reviewing relational DDL syntax and constraint definitions.
-- - Team implementation: All 3NF relational schemas, primary/foreign keys, check
--   constraints, and table architectures were independently designed and verified by
--   TUT19-AssignmentGroup-08:
--   Rao Fu (520310746), Yicheng Xie (540610813), Haoran Lu (550118350), Yuanyuan Wang (541012696).
-- ==============================================================================

-- Complete Task 4 DDL. Run once in a NEW empty DuckDB database.
-- Install spatial for this platform/version before running.

-- COMP5339 Task 4. Execute in a NEW database; load with build_storage.py.
-- Requires DuckDB 1.5.5 and its platform-specific spatial extension.
LOAD spatial;
SET geometry_always_xy = true;
SET TimeZone = 'UTC';
CREATE SCHEMA core;
CREATE SCHEMA audit;
CREATE SCHEMA analytics;

CREATE TABLE core.operator (
    operator_id INTEGER PRIMARY KEY,
    operator_name VARCHAR NOT NULL UNIQUE
);
CREATE TABLE core.operator_profile (
    profile_id INTEGER PRIMARY KEY,
    operator_id INTEGER NOT NULL REFERENCES core.operator(operator_id),
    website VARCHAR,
    contact_phone VARCHAR,
    contact_email VARCHAR
);
CREATE TABLE core.sa4_region (
    sa4_code VARCHAR PRIMARY KEY,
    sa4_name VARCHAR NOT NULL,
    state_code VARCHAR NOT NULL,
    state_name VARCHAR NOT NULL,
    gcc_code VARCHAR NOT NULL,
    gcc_name VARCHAR NOT NULL,
    area_sqkm DOUBLE CHECK (area_sqkm >= 0),
    boundary_year INTEGER NOT NULL CHECK (boundary_year = 2026),
    geom GEOMETRY('EPSG:7844')
);
CREATE TABLE core.charger_record (
    ev_id BIGINT PRIMARY KEY,
    operator_id INTEGER NOT NULL REFERENCES core.operator(operator_id),
    sa4_code VARCHAR NOT NULL REFERENCES core.sa4_region(sa4_code),
    station_name VARCHAR,
    station_address VARCHAR NOT NULL,
    postcode VARCHAR CHECK (postcode IS NULL OR regexp_full_match(postcode, '[0-9]{4}')),
    lga_name VARCHAR,
    charger_type VARCHAR NOT NULL CHECK (charger_type IN ('AC', 'DC', 'Upcoming')),
    power_kw DOUBLE CHECK (power_kw > 0),
    power_kw_min DOUBLE CHECK (power_kw_min > 0 AND power_kw_min <= power_kw),
    plugs_count INTEGER NOT NULL CHECK (plugs_count >= 0),
    longitude DOUBLE NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    latitude DOUBLE NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    geom GEOMETRY('EPSG:7844') NOT NULL,
    sa4_match_method VARCHAR NOT NULL CHECK (sa4_match_method IN ('within','nearest_within_tolerance')),
    sa4_match_distance_m DOUBLE NOT NULL CHECK (sa4_match_distance_m BETWEEN 0 AND 25),
    same_location_size INTEGER NOT NULL CHECK (same_location_size >= 1),
    rating_format VARCHAR NOT NULL
);
CREATE TABLE core.dc_augmentation (
    ev_id BIGINT PRIMARY KEY REFERENCES core.charger_record(ev_id),
    operator_profile_id INTEGER REFERENCES core.operator_profile(profile_id),
    match_status VARCHAR NOT NULL,
    match_reason VARCHAR,
    coordinate_group VARCHAR NOT NULL,
    site_success BOOLEAN NOT NULL,
    operator_success BOOLEAN NOT NULL,
    overall_success BOOLEAN NOT NULL,
    usage_type VARCHAR,
    membership_required BOOLEAN,
    pricing_text VARCHAR,
    bay_count INTEGER CHECK (bay_count >= 0),
    vehicle_access VARCHAR,
    candidate_keys JSON NOT NULL,
    attribute_conflicts JSON NOT NULL,
    reviewed BOOLEAN NOT NULL,
    input_address_conflict BOOLEAN NOT NULL,
    CHECK (overall_success = (site_success OR operator_success))
);
CREATE TABLE core.plug_type (
    plug_type VARCHAR PRIMARY KEY
);
CREATE TABLE core.charger_plug (
    ev_id BIGINT NOT NULL REFERENCES core.dc_augmentation(ev_id),
    plug_type VARCHAR NOT NULL REFERENCES core.plug_type(plug_type),
    PRIMARY KEY (ev_id, plug_type)
);
CREATE TABLE core.dc_connection (
    connection_row_id BIGINT PRIMARY KEY,
    ev_id BIGINT NOT NULL REFERENCES core.dc_augmentation(ev_id),
    candidate_key VARCHAR NOT NULL,
    connection_id VARCHAR NOT NULL,
    connection_type_id VARCHAR NOT NULL,
    plug_type VARCHAR NOT NULL REFERENCES core.plug_type(plug_type),
    current_type VARCHAR NOT NULL CHECK (current_type = 'DC'),
    power_kw_external DOUBLE CHECK (power_kw_external > 0),
    quantity_external INTEGER CHECK (quantity_external >= 0),
    UNIQUE (ev_id, candidate_key, connection_id)
);
-- Each evidence row is an assertion applied to an input record, not a new station.
-- Operator attributes themselves live once per operator in operator_profile.
CREATE TABLE core.attribute_evidence (
    evidence_id BIGINT PRIMARY KEY,
    ev_id BIGINT NOT NULL REFERENCES core.dc_augmentation(ev_id),
    scope VARCHAR NOT NULL CHECK (scope IN ('operator','site')),
    field VARCHAR NOT NULL,
    value_json JSON NOT NULL,
    candidate_key VARCHAR,
    source VARCHAR NOT NULL,
    external_id VARCHAR NOT NULL,
    source_url VARCHAR NOT NULL,
    raw_file VARCHAR NOT NULL,
    raw_sha256 VARCHAR NOT NULL CHECK (length(raw_sha256) = 64),
    retrieved_at_utc TIMESTAMPTZ NOT NULL,
    evidence_text VARCHAR,
    provider_name VARCHAR,
    provider_license VARCHAR,
    date_last_verified TIMESTAMPTZ
);
CREATE TABLE audit.build_metadata (
    metadata_key VARCHAR PRIMARY KEY,
    metadata_value JSON NOT NULL
);

-- Exact typed handoff snapshot, intentionally denormalized for audit.
CREATE TABLE audit.input_snapshot (
    "OBJECTID" VARCHAR,
    "Station_name" VARCHAR,
    "Station_address" VARCHAR,
    "Operator" VARCHAR,
    "Number_of_plugs" VARCHAR,
    "Charger_Type" VARCHAR,
    "Charger_rating" VARCHAR,
    "Latitude" DOUBLE,
    "Longitude" DOUBLE,
    "LGANAME" VARCHAR,
    "PCODE" VARCHAR,
    "Source" VARCHAR,
    "ev_id" BIGINT PRIMARY KEY,
    "station_name_missing" BOOLEAN,
    "objectid_missing" BOOLEAN,
    "lga_missing" BOOLEAN,
    "pcode_missing" BOOLEAN,
    "source_missing" BOOLEAN,
    "pcode_clean" VARCHAR,
    "pcode_was_malformed" BOOLEAN,
    "operator_clean" VARCHAR,
    "power_kw" DOUBLE,
    "power_kw_min" DOUBLE,
    "rating_format" VARCHAR,
    "rating_is_compound" BOOLEAN,
    "rating_was_invalid" BOOLEAN,
    "rating_missing_unit" BOOLEAN,
    "plugs_count" BIGINT,
    "is_upcoming" BOOLEAN,
    "is_exact_duplicate" BOOLEAN,
    "same_location_size" BIGINT,
    "same_location_group" BOOLEAN,
    "longitude_gda2020" DOUBLE,
    "latitude_gda2020" DOUBLE,
    "sa4_code" VARCHAR,
    "sa4_name" VARCHAR,
    "sa4_state_code" VARCHAR,
    "sa4_state_name" VARCHAR,
    "sa4_gcc_name" VARCHAR,
    "sa4_match_method" VARCHAR,
    "sa4_match_distance_m" DOUBLE,
    "aug_operator_clean" VARCHAR,
    "aug_coordinate_group" VARCHAR,
    "aug_match_status" VARCHAR,
    "aug_match_reason" VARCHAR,
    "aug_ocm_retrieved" BOOLEAN,
    "aug_site_augmentation_success" BOOLEAN,
    "aug_operator_augmentation_success" BOOLEAN,
    "aug_augmentation_success" BOOLEAN,
    "aug_candidate_keys_json" VARCHAR,
    "aug_plug_types_json" VARCHAR,
    "aug_usage_type" VARCHAR,
    "aug_membership_required" BOOLEAN,
    "aug_pricing_text" VARCHAR,
    "aug_bay_count" BIGINT,
    "aug_vehicle_access" INTEGER,
    "aug_operator_website" VARCHAR,
    "aug_operator_contact_phone" VARCHAR,
    "aug_operator_contact_email" VARCHAR,
    "aug_attribute_conflicts_json" VARCHAR,
    "aug_input_address_conflict" BOOLEAN,
    "aug_reviewed" BOOLEAN,
    "aug_input_sha256" VARCHAR,
    "aug_rules_sha256" VARCHAR,
    "aug_candidate_sha256" VARCHAR
);

CREATE INDEX charger_geom_rtree ON core.charger_record USING RTREE (geom);
CREATE INDEX region_geom_rtree ON core.sa4_region USING RTREE (geom);
CREATE INDEX charger_sa4_idx ON core.charger_record (sa4_code);
CREATE INDEX charger_operator_idx ON core.charger_record (operator_id);
-- Exactly one row per original ev_id; connector detail is NOT joined here.
CREATE VIEW analytics.chargers AS
SELECT c.*, o.operator_name, r.sa4_name, r.state_name, r.gcc_name,
       a.match_status, a.site_success, a.operator_success, a.overall_success,
       a.usage_type, a.membership_required, a.pricing_text, a.bay_count,
       a.vehicle_access, a.attribute_conflicts,
       CASE WHEN a.operator_success THEN p.website END AS operator_website,
       CASE WHEN a.operator_success THEN p.contact_phone END AS operator_contact_phone,
       CASE WHEN a.operator_success THEN p.contact_email END AS operator_contact_email
FROM core.charger_record c
JOIN core.operator o USING (operator_id)
JOIN core.sa4_region r USING (sa4_code)
LEFT JOIN core.dc_augmentation a USING (ev_id)
LEFT JOIN core.operator_profile p ON p.profile_id = a.operator_profile_id;

CREATE VIEW analytics.sa4_coverage AS
SELECT r.sa4_code, r.sa4_name, r.area_sqkm,
       count(c.ev_id) AS charger_records,
       count(c.ev_id) FILTER (WHERE c.charger_type='DC') AS dc_records,
       count(c.ev_id) FILTER (WHERE c.charger_type='AC') AS ac_records,
       count(c.ev_id) FILTER (WHERE c.charger_type='Upcoming') AS upcoming_records,
       count(c.ev_id) FILTER (WHERE a.site_success) AS site_augmented_dc_records,
       count(c.ev_id) FILTER (WHERE a.overall_success) AS augmented_dc_records
FROM core.sa4_region r
LEFT JOIN core.charger_record c USING (sa4_code)
LEFT JOIN core.dc_augmentation a USING (ev_id)
WHERE r.geom IS NOT NULL
GROUP BY r.sa4_code, r.sa4_name, r.area_sqkm;

CREATE VIEW analytics.dc_augmentation_coverage AS
SELECT count(*) AS dc_records,
       count(*) FILTER (WHERE site_success) AS site_augmented,
       count(*) FILTER (WHERE operator_success) AS operator_augmented,
       count(*) FILTER (WHERE overall_success) AS overall_augmented,
       100.0 * count(*) FILTER (WHERE site_success) / nullif(count(*),0) AS site_coverage_pct,
       100.0 * count(*) FILTER (WHERE overall_success) / nullif(count(*),0) AS overall_coverage_pct
FROM core.dc_augmentation;
