"""Supplementary transactional audit database (Problem 5, Stage 2).

append_event() is called INSIDE the caller's transaction on a connection that has audit.db
attached as schema "audit", so a database change and its audit event commit or roll back
together. verify_chain() recomputes the hash chain.

This is NOT the required agent audit trail (output/audit_trail.json, Stage 4).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent / "audit_schema.sql"
GENESIS = "0" * 64
HASHED_FIELDS = ("ts_utc", "business_date", "actor", "actor_kind", "run_id", "run_mode", "call_id",
                 "event_type", "ref_table", "ref_id", "payload", "prev_hash")


REQUIRED_OBJECTS = {("table", "events"), ("table", "audit_meta"),
                    ("trigger", "events_no_update"), ("trigger", "events_no_delete")}


def ensure(audit_path: Path) -> None:
    """Create audit.db and its schema if needed (privileged, outside any restricted connection).

    Runs only the fixed, version-controlled statements in audit_schema.sql (CREATE ... IF NOT EXISTS
    and an INSERT ... WHERE NOT EXISTS), so it can add missing objects but never drop, delete, or
    overwrite existing history. Does nothing once the schema is present. Callers pass paths.audit_db.
    """
    if Path(audit_path).name != "audit.db":
        raise ValueError(f"audit initializer only operates on a file named audit.db, not {audit_path}")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(audit_path, isolation_level=None, timeout=5.0)) as con:
        present = set(con.execute("SELECT type, name FROM sqlite_master"))
        if REQUIRED_OBJECTS <= present:
            return
        con.execute("PRAGMA journal_mode = DELETE")
        con.executescript(SCHEMA.read_text(encoding="utf-8"))


def event_hash(fields: dict) -> str:
    canonical = json.dumps({k: fields[k] for k in HASHED_FIELDS}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def append_event(con: sqlite3.Connection, *, actor: str, actor_kind: str, event_type: str, payload: dict,
                 business_date: str | None = None, run_id: str | None = None, run_mode: str | None = None,
                 call_id: str | None = None, ref_table: str | None = None, ref_id: int | None = None,
                 schema: str = "audit") -> int:
    """Insert one hash-chained event. Must run inside the caller's open write transaction."""
    if not con.in_transaction:
        raise RuntimeError("append_event must run inside the caller's transaction")
    last = con.execute(f"SELECT hash FROM {schema}.events ORDER BY id DESC LIMIT 1").fetchone()
    fields = {
        "ts_utc": utc_now(), "business_date": business_date, "actor": actor, "actor_kind": actor_kind,
        "run_id": run_id, "run_mode": run_mode, "call_id": call_id, "event_type": event_type,
        "ref_table": ref_table, "ref_id": ref_id,
        "payload": json.dumps(payload, sort_keys=True, default=str),
        "prev_hash": last[0] if last else GENESIS,
    }
    fields["hash"] = event_hash(fields)
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    return con.execute(f"INSERT INTO {schema}.events ({cols}) VALUES ({marks})", tuple(fields.values())).lastrowid


def verify_chain(audit_path: Path) -> dict:
    """Recompute every hash and link. Reports problems; never repairs."""
    problems: list[str] = []
    with closing(sqlite3.connect(Path(audit_path).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM events ORDER BY id").fetchall()
    expected_prev = GENESIS
    for r in rows:
        fields = dict(r)
        if fields["prev_hash"] != expected_prev:
            problems.append(f"event {r['id']}: prev_hash does not match the previous event")
        if event_hash(fields) != fields["hash"]:
            problems.append(f"event {r['id']}: content does not match its hash")
        expected_prev = fields["hash"]
    return {"events": len(rows), "valid": not problems, "problems": problems}
