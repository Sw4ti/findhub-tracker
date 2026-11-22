CREATE TABLE IF NOT EXISTS detections (
    fingerprint text PRIMARY KEY,
    device text NOT NULL,
    label text NOT NULL,
    source text NOT NULL,
    report_id text,
    reported timestamptz NOT NULL,
    collected timestamptz NOT NULL DEFAULT now(),
    latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    accuracy double precision CHECK (accuracy >= 0),
    status text NOT NULL
);
CREATE INDEX IF NOT EXISTS detections_history ON detections (source, device, reported, fingerprint);
CREATE TABLE IF NOT EXISTS sessions (
    digest text PRIMARY KEY,
    expires timestamptz NOT NULL,
    csrf_digest text,
    actor text NOT NULL DEFAULT ''
);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS csrf_digest text;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS actor text NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS login_attempts (
    attempted timestamptz NOT NULL DEFAULT now(),
    client_key text NOT NULL DEFAULT 'legacy'
);
ALTER TABLE login_attempts ADD COLUMN IF NOT EXISTS client_key text NOT NULL DEFAULT 'legacy';
CREATE INDEX IF NOT EXISTS login_attempts_recent ON login_attempts (attempted, client_key);
CREATE TABLE IF NOT EXISTS poll_state (
    source text PRIMARY KEY,
    attempted timestamptz,
    succeeded timestamptz,
    error text
);
CREATE TABLE IF NOT EXISTS discovery_state (
    id integer PRIMARY KEY CHECK (id=1),
    status text NOT NULL DEFAULT 'never',
    attempted timestamptz,
    succeeded timestamptz,
    error text
);
INSERT INTO discovery_state(id,status) VALUES (1,'never') ON CONFLICT (id) DO NOTHING;
CREATE TABLE IF NOT EXISTS discovered_devices (
    device text PRIMARY KEY,
    label text NOT NULL,
    account_status text NOT NULL CHECK (account_status IN ('active','unavailable','removed')),
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen timestamptz NOT NULL DEFAULT now(),
    missing_successes integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS deleted_devices (
    device text PRIMARY KEY,
    label text NOT NULL,
    deleted_at timestamptz NOT NULL DEFAULT now(),
    actor text NOT NULL,
    operation_id text NOT NULL,
    checkpoint_file text NOT NULL,
    checkpoint_sha256 text NOT NULL,
    detections_deleted integer NOT NULL CHECK (detections_deleted >= 0),
    collector_log_rows_deleted integer NOT NULL DEFAULT 0 CHECK (collector_log_rows_deleted >= 0)
);
CREATE TABLE IF NOT EXISTS device_deletion_jobs (
    device text PRIMARY KEY,
    operation_id text NOT NULL UNIQUE,
    actor text NOT NULL,
    state text NOT NULL CHECK (state IN ('preparing','checkpointed','collector_removed','database_deleted','recovery_required')),
    checkpoint_file text,
    checkpoint_sha256 text,
    detections_expected integer,
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE device_deletion_jobs DROP CONSTRAINT IF EXISTS device_deletion_jobs_state_check;
ALTER TABLE device_deletion_jobs ADD CONSTRAINT device_deletion_jobs_state_check
    CHECK (state IN ('preparing','checkpointed','collector_removed','database_deleted','recovery_required'));
CREATE TABLE IF NOT EXISTS device_deletion_events (
    id bigserial PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    device text NOT NULL,
    actor text NOT NULL,
    detections_deleted integer NOT NULL CHECK (detections_deleted >= 0),
    registration_deleted integer NOT NULL CHECK (registration_deleted IN (0,1)),
    collector_log_rows_deleted integer NOT NULL DEFAULT 0 CHECK (collector_log_rows_deleted >= 0),
    checkpoint_file text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('complete','recovery_required','restored'))
);
ALTER TABLE device_deletion_events DROP CONSTRAINT IF EXISTS device_deletion_events_outcome_check;
ALTER TABLE device_deletion_events ADD CONSTRAINT device_deletion_events_outcome_check
    CHECK (outcome IN ('complete','recovery_required','restored'));
