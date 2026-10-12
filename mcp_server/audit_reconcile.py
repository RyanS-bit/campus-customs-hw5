"""Reconcile output/audit_trail.json with the supplementary audit.db and the working database
(Problem 5, Stage 4; spec section 7.6, architecture section 19.2).

check()      reports; never writes.
reconcile()  appends ONE 'incomplete_operation_reconciled' system event per incomplete tool call
             (a tool_call_started with no tool_call_finished). It never edits or deletes earlier
             events, never claims a rollback, and never records the same gap twice.

db_outcome for an incomplete call:
  committed          audit.db has an event with this run_id and call_id, and its row (same id, run_id
                     and call_id) exists
  committed_row_no_longer_present
                     audit.db proves the commit, but the row is gone or its id now belongs to another
                     run (the working database was reset since); never matched by id alone
  not_found          audit.db is readable and holds no such event (no matching change was found;
                     this does NOT prove a rollback; for read tools no change is expected)
  indeterminate      audit.db or the working database could not be read

Problem 7 adds two more kinds of incomplete operation. Neither the JSON trail nor SQLite can be committed
atomically with the other, so a gap between them is DETECTED and RECONCILED here, never assumed away:
  human decision     a 'human_decision_requested' with no 'human_decision_finished' (same call_id = decision_id).
                     The decision's database change and its audit.db events commit in ONE transaction, so:
                     committed (audit.db has events for this decision_id and their rows still match),
                     committed_row_no_longer_present (audit.db proves it; the rows were replaced by a later reset),
                     not_found (audit.db is readable and has no event for it: nothing from it was committed),
                     indeterminate (audit.db or the working database unreadable).
  reset              a 'db_reset_requested' with no 'db_reset' outcome (same reset_id). The swap is atomic and the
                     new working copy carries the reset_id stamp, so: committed (the working copy carries this
                     stamp), not_found (no stamp and no later reset: the working copy was not replaced by it),
                     indeterminate (a later reset replaced the copy, or it is unreadable).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import audit_db
import audit_trail
import locks
import maintenance
from paths import Paths


class ReconcileRefused(RuntimeError):
    """Reconciliation was refused because an agent run is active."""

WRITE_TOOLS = ("propose_payment", "propose_purchase", "propose_quote", "draft_message")


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _db_events(paths: Paths) -> list[dict] | None:
    if not paths.audit_db.exists():
        return []
    try:
        with closing(_ro(paths.audit_db)) as con:
            return [dict(r) for r in con.execute(
                "SELECT id, run_id, call_id, event_type, ref_table, ref_id, actor, actor_kind, payload FROM events ORDER BY id")]
    except sqlite3.Error:
        return None


def _row_exists(paths: Paths, table: str, row_id: int, run_id: str, call_id: str) -> bool | None:
    """True only if the row with this id was written by THIS run and call (remediation M4: ids restart
    after a reset, so an id alone can point at a different run's row)."""
    if table not in ("pending_actions", "drafts"):
        return None
    try:
        with closing(_ro(paths.working_db)) as con:
            return con.execute(f"SELECT 1 FROM {table} WHERE id = ? AND run_id = ? AND call_id IS ?",
                               (row_id, run_id, call_id)).fetchone() is not None
    except sqlite3.Error:
        return None


def _kind(e: dict) -> str:
    return {"tool_call_started": "tool_call", "human_decision_requested": "human_decision",
            "db_reset_requested": "db_reset"}[e["event_type"]]


def _incomplete(events: list[dict]) -> list[dict]:
    # A tool call counts as finished only if its finish event has a known outcome; a call recorded
    # as cancelled ('incomplete') is still examined against the databases (remediation m8).
    finished = {(e["run_id"], e["call_id"]) for e in events if e["event_type"] == "tool_call_finished"
                and (e.get("outcome") or {}).get("status") != "incomplete"}
    decided = {e["call_id"] for e in events if e["event_type"] == "human_decision_finished"}
    reset_done = {(e.get("details") or {}).get("reset_id") for e in events if e["event_type"] == "db_reset"}
    reconciled = {(e.get("details") or {}).get("started_event_id") for e in events
                  if e["event_type"] == "incomplete_operation_reconciled"}
    out = []
    for e in events:
        if e["event_id"] in reconciled:
            continue
        if e["event_type"] == "tool_call_started" and (e["run_id"], e["call_id"]) not in finished:
            out.append(e)
        elif e["event_type"] == "human_decision_requested" and e["call_id"] not in decided:
            out.append(e)
        elif e["event_type"] == "db_reset_requested" and (e.get("details") or {}).get("reset_id") not in reset_done:
            out.append(e)
    return out


def _row_matches(con, d: dict, payload: dict) -> bool:
    """Does the row that a human decision's audit.db event refers to still hold what the event says?
    (Ids restart after a reset, so an id alone is never enough.)"""
    t, rid = d["ref_table"], d["ref_id"]
    if t == "payments":
        r = con.execute("SELECT kind, ref_id, amount, approved_by, paid_at FROM payments WHERE id = ?", (rid,)).fetchone()
        return r is not None and tuple(r) == (payload.get("kind"), payload.get("ref_id"), payload.get("amount"),
                                              payload.get("approved_by"), payload.get("paid_at"))
    if t == "pending_actions":
        r = con.execute("SELECT decided_by, decided_at, run_id FROM pending_actions WHERE id = ?", (rid,)).fetchone()
        return r is not None and tuple(r) == (payload.get("decided_by"), payload.get("decided_at"),
                                              payload.get("proposal_run_id"))
    if t == "tickets":
        r = con.execute("SELECT status FROM tickets WHERE id = ?", (rid,)).fetchone()
        return r is not None and r[0] == payload.get("status_after")
    return False


def _human_outcome(started: dict, db_events: list[dict] | None, paths: Paths) -> dict:
    d0 = started.get("details") or {}
    base = {"decision_id": started["call_id"], "decided_by": started["actor"], "operation": d0.get("operation"),
            "action_id": d0.get("action_id"), "ticket_id": d0.get("ticket_id")}
    if db_events is None:
        return {**base, "db_outcome": "indeterminate", "reason": "audit.db could not be read"}
    mine = [d for d in db_events if d["actor_kind"] == "human" and d["call_id"] == started["call_id"]]
    if not mine:
        return {**base, "db_outcome": "not_found",
                "reason": "audit.db is readable and holds no event for this decision. The decision's database change and "
                          "its audit.db events commit in one transaction, so nothing from this decision was committed."}
    links = {"audit_db_event_ids": [d["id"] for d in mine], "audit_db_event_types": [d["event_type"] for d in mine],
             "payment_ids": [d["ref_id"] for d in mine if d["ref_table"] == "payments"],
             "rows": [f"{d['ref_table']}:{d['ref_id']}" for d in mine]}
    try:
        with closing(_ro(paths.working_db)) as con:
            present = all(_row_matches(con, d, json.loads(d["payload"])) for d in mine)
    except (sqlite3.Error, ValueError):
        return {**base, **links, "db_outcome": "indeterminate",
                "reason": "audit.db proves the decision committed, but the working database could not be read"}
    if not present:
        return {**base, **links, "db_outcome": "committed_row_no_longer_present",
                "reason": "audit.db proves the decision committed (same transaction), but the working database no longer "
                          "holds those rows as recorded; it was most likely reset since"}
    return {**base, **links, "db_outcome": "committed",
            "reason": "audit.db events for this decision exist and the working database still holds the recorded rows"}


def _reset_outcome(started: dict, events: list[dict], paths: Paths) -> dict:
    reset_id = (started.get("details") or {}).get("reset_id")
    base = {"reset_id": reset_id, "requested_by": (started.get("details") or {}).get("requested_by")}
    later = [e for e in events if e["seq"] > started["seq"] and e["event_type"] in ("db_reset", "db_reset_requested")
             and (e.get("details") or {}).get("reset_id") != reset_id]
    try:
        stamp = maintenance.read_reset_stamp(paths.working_db) if paths.working_db.exists() else None
    except sqlite3.Error:
        return {**base, "db_outcome": "indeterminate", "reason": "the working database could not be read"}
    if stamp == reset_id:
        return {**base, "db_outcome": "committed",
                "reason": "the working copy carries this reset's stamp: the reset took effect"}
    if later:
        return {**base, "db_outcome": "indeterminate",
                "reason": f"a later reset (trail seq {later[0]['seq']}) replaced the working copy, so whether this reset "
                          "took effect cannot be shown from the working copy"}
    return {**base, "db_outcome": "not_found",
            "reason": "the working copy does not carry this reset's stamp and no later reset is recorded; the atomic swap "
                      "did not take effect, so this reset did not replace the working copy"}


def _outcome_for(started: dict, db_events: list[dict] | None, paths: Paths, events: list[dict] | None = None) -> dict:
    if started["event_type"] == "human_decision_requested":
        return _human_outcome(started, db_events, paths)
    if started["event_type"] == "db_reset_requested":
        return _reset_outcome(started, events or [], paths)
    if db_events is None:
        return {"db_outcome": "indeterminate", "reason": "audit.db could not be read"}
    match = [d for d in db_events if d["run_id"] == started["run_id"] and d["call_id"] == started["call_id"]]
    if not match:
        reason = ("read-only tool: no database change is expected" if started["tool"] not in WRITE_TOOLS
                  else "no audit.db event with this run_id and call_id; no matching change was found")
        return {"db_outcome": "not_found", "reason": reason}
    d = match[0]
    exists = _row_exists(paths, d["ref_table"], d["ref_id"], started["run_id"], started["call_id"])
    if exists is None:
        return {"db_outcome": "indeterminate", "reason": "working database could not be read",
                "audit_db_event_id": d["id"]}
    if not exists:
        return {"db_outcome": "committed_row_no_longer_present",
                "reason": ("audit.db proves the change committed (same transaction), but the working database no "
                           "longer holds that row for this run and call; it was most likely reset since"),
                "audit_db_event_id": d["id"], "ref_table": d["ref_table"], "ref_id": d["ref_id"]}
    return {"db_outcome": "committed", "audit_db_event_id": d["id"], "ref_table": d["ref_table"], "ref_id": d["ref_id"]}


def check(paths: Paths, trail: audit_trail.AuditTrail | None = None) -> dict:
    trail = trail or audit_trail.AuditTrail(paths.audit_trail_json)
    json_report = trail.verify()
    db_report = audit_db.verify_chain(paths.audit_db) if paths.audit_db.exists() else {"events": 0, "valid": True, "problems": []}
    problems: list[str] = []
    try:
        events = trail.read()
    except audit_trail.AuditTrailError as exc:
        return {"json": json_report, "audit_db": db_report, "incomplete": [], "cross_reference_problems": [str(exc)],
                "consistent": False}
    db_events = _db_events(paths)
    finished = {(e["run_id"], e["call_id"]): e for e in events if e["event_type"] == "tool_call_finished"}
    if db_events is not None:
        for d in db_events:
            if d["run_id"] and d["call_id"] and (d["run_id"], d["call_id"]) not in finished:
                started = any(e["event_type"] == "tool_call_started" and (e["run_id"], e["call_id"]) == (d["run_id"], d["call_id"])
                              for e in events)
                if not started:
                    problems.append(f"audit.db event {d['id']} (run {d['run_id']}, call {d['call_id']}) has no JSON tool call")
        by_id = {d["id"]: d for d in db_events}
        for f in finished.values():
            link = (f.get("links") or {}).get("audit_db_event_id")
            if link is not None:
                d = by_id.get(link)
                if d is None or (d["run_id"], d["call_id"]) != (f["run_id"], f["call_id"]):
                    problems.append(f"JSON event {f['seq']} links audit.db event {link}, which does not match")
        # Problem 7: every human decision in audit.db must have been requested in the JSON trail first.
        requested = {e["call_id"] for e in events if e["event_type"] == "human_decision_requested"}
        for d in db_events:
            if d["actor_kind"] == "human" and d["call_id"] not in requested:
                problems.append(f"audit.db human event {d['id']} ({d['event_type']}, decision {d['call_id']}) has no "
                                "JSON human_decision_requested")
    run_active = locks.run_active(paths.active_dir)
    incomplete = [{"kind": _kind(e), "started_seq": e["seq"], "started_event_id": e["event_id"], "run_id": e["run_id"],
                   "call_id": e["call_id"], "tool": e["tool"], "actor": e["actor"],
                   **_outcome_for(e, db_events, paths, events),
                   # Remediation M3: while a run (or a decision or reset) holds the lock, the operation may still be in progress.
                   **({"in_progress": True, "note": "the run lock is held; this operation may still be in progress"}
                      if run_active else {})}
                  for e in _incomplete(events)]
    return {"json": json_report, "audit_db": db_report, "incomplete": incomplete,
            "cross_reference_problems": problems, "run_active": run_active,
            "consistent": json_report["valid"] and db_report["valid"] and not problems and not incomplete}


def reconcile(paths: Paths, trail: audit_trail.AuditTrail | None = None) -> list[dict]:
    """Append one reconciliation event per unreconciled incomplete call. Idempotent."""
    trail = trail or audit_trail.AuditTrail(paths.audit_trail_json)
    # Remediation M3: hold the run lock for the whole reconciliation. If a run is active we refuse,
    # and while we reconcile no run can start (RunRecorder takes the same lock without waiting).
    run_lock = locks.file_lock(locks.run_lock_path(paths.active_dir), timeout=0)
    try:
        run_lock.__enter__()
    except locks.LockTimeout as exc:
        raise ReconcileRefused("An agent run is active (run lock held); reconcile after it finishes.") from exc
    try:
        return _reconcile_locked(paths, trail)
    finally:
        run_lock.__exit__(None, None, None)


NOTES = {
    "tool_call": "Recorded after the fact. The tool call's completion event was never saved; this states what the "
                 "databases show and does not claim a rollback.",
    "human_decision": "Recorded after the fact. The decision's completion event was never saved to the JSON trail (the "
                      "JSON file cannot be committed atomically with SQLite); this states what audit.db and the working "
                      "database show. decided_by is the name recorded with the request (attribution only).",
    "db_reset": "Recorded after the fact. The reset's outcome event was never saved; this states what the working copy's "
                "reset stamp shows.",
}


def reconcile_locked(paths: Paths, trail: audit_trail.AuditTrail) -> list[dict]:
    """Reconcile for a caller that ALREADY holds the run lock (the human decision path and the controlled reset run
    this first, so an earlier gap is always closed before anything new happens)."""
    db_events = _db_events(paths)

    def build(events):
        new = []
        for e in _incomplete(events):                     # decided under the trail lock: no double recording
            kind = _kind(e)
            result = _outcome_for(e, db_events, paths, events)
            new.append(audit_trail.system_event(
                "incomplete_operation_reconciled",
                outcome={"status": "incomplete", "db_outcome": result["db_outcome"]},
                details={"kind": kind, "started_event_id": e["event_id"], "started_seq": e["seq"], "run_id": e["run_id"],
                         "call_id": e["call_id"], "tool": e["tool"], "actor": e["actor"], **result,
                         "note": NOTES[kind]}))
        return new

    return trail.append_with(build)


_reconcile_locked = reconcile_locked
