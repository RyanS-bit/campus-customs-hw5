-- Supplementary audit database (Problem 5, Stage 2). Lives beside the working copy (paths.audit_db).
-- Records ONLY changes to the working database, written in the SAME transaction as the change.
-- It does not replace the required output/audit_trail.json (agent-loop events, Stage 4).
-- Never touched by the reset (R8). Idempotent: safe to run on an existing file.
-- BEGIN IMMEDIATE takes the write lock up front: a deferred transaction that reads and then
-- upgrades to a write lock fails at once with "database is locked" under concurrency.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS audit_meta (
    version     INTEGER NOT NULL,
    description TEXT    NOT NULL
);
INSERT INTO audit_meta
    SELECT 1, 'hash-chained, append-only database-change events' WHERE NOT EXISTS (SELECT 1 FROM audit_meta);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc        TEXT    NOT NULL,                 -- wall clock, labelled as such
    business_date TEXT,                             -- desk.date_today (R5)
    actor         TEXT    NOT NULL,                 -- agent name, 'system', or 'human:<name>'
    actor_kind    TEXT    NOT NULL CHECK (actor_kind IN ('agent', 'system', 'human')),
    run_id        TEXT,
    run_mode      TEXT    CHECK (run_mode IS NULL OR run_mode IN ('simulated', 'live')),
    call_id       TEXT,                             -- correlation with audit_trail.json only
    event_type    TEXT    NOT NULL,
    ref_table     TEXT,
    ref_id        INTEGER,
    payload       TEXT    NOT NULL CHECK (json_valid(payload)),
    prev_hash     TEXT    NOT NULL UNIQUE CHECK (length(prev_hash) = 64),   -- UNIQUE: the chain cannot fork
    hash          TEXT    NOT NULL UNIQUE CHECK (length(hash) = 64)
);

-- Guards against ordinary application edits only; anyone with file access can still alter the
-- file. Edits are then DETECTABLE through the hash chain (audit_db.verify_chain), not prevented.
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'audit events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'audit events are append-only');
END;

COMMIT;
