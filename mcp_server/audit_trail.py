"""Required agent audit trail: output/audit_trail.json (Problem 5, Stage 4).

One valid JSON array of hash-chained events. Every append:
  1. takes an OS file lock (serialised writers; released automatically if a writer crashes),
  2. reads and VERIFIES the whole existing file (refuses to write if it is damaged: fail-closed),
  3. writes old events + new events to a temporary file in the same folder, flushes and fsyncs it,
  4. atomically replaces the original (os.replace). A crash leaves either the old or the new file.
Earlier events are never rewritten: their content and hashes are carried over unchanged.

Only REAL events are recorded. Each event says who acted (trusted identity supplied by the
caller, never by a model), whether the run was 'live' or 'simulated', and what happened.
Usage figures are recorded only when the caller supplies them; nothing is estimated.

This module touches no database. Reconciliation with audit.db is in audit_reconcile.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import locks

GENESIS = "0" * 64
AGENTS = ("boss", "inventory", "accounting", "facilities", "customer_service")
MODES = ("live", "simulated")
ACTOR_KINDS = ("agent", "system", "human")
OUTCOMES = ("ok", "refused", "error", "incomplete")
EVENT_TYPES = (
    "trail_initialized",
    "run_started", "run_finished",
    "agent_started", "agent_finished",
    "delegation_requested", "delegation_completed", "delegation_refused",
    "tool_call_started", "tool_call_finished",
    "budget_exceeded", "error",
    "db_reset", "db_snapshot",
    "incomplete_operation_reconciled",
    # Problem 7: a human decision (approve / reject / resolve), recorded before and after its transaction
    "human_decision_requested", "human_decision_finished",
    # Problem 7 (A3): a reset requested through the backend, recorded BEFORE it starts; 'db_reset' records its outcome
    "db_reset_requested",
)
# Fields a caller may set. seq, run_seq, prev_hash and hash are assigned by the writer.
CALLER_FIELDS = ("event_id", "ts_utc", "event_type", "actor", "actor_kind", "mode", "run_id", "business_date",
                 "chain", "ticket_id", "tool", "args", "call_id", "outcome", "result_summary", "result_sha256",
                 "links", "usage", "details")
WRITER_FIELDS = ("seq", "run_seq", "prev_hash", "hash")


class AuditTrailError(RuntimeError):
    """The trail could not be read, verified or written. Callers must fail closed."""


class AuditTrailCorrupt(AuditTrailError):
    pass


class DuplicateEventConflict(AuditTrailError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def event_hash(event: dict) -> str:
    return sha256_text(canonical({k: v for k, v in event.items() if k != "hash"}))


def make_event(event_type: str, *, actor: str, actor_kind: str, mode: str | None = None, run_id: str | None = None,
               **fields) -> dict:
    """Build a complete caller event (with event_id and timestamp) so retries reuse identical content."""
    event = {"event_id": str(uuid.uuid4()), "ts_utc": utc_now(), "event_type": event_type, "actor": actor,
             "actor_kind": actor_kind, "mode": mode, "run_id": run_id}
    event.update(fields)
    for name in CALLER_FIELDS:
        event.setdefault(name, None)
    return event


# One pattern for "this environment variable holds a credential", shared with the runner's
# subprocess-environment filter (remediation m6).
CREDENTIAL_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL", re.IGNORECASE)
_REGISTERED_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Secrets loaded from files (e.g. PORTKEY_API_KEY via dotenv) are not in os.environ; the
    configuration registers them here so the guard below can see them (remediation m2)."""
    if value and len(value) >= 8:
        _REGISTERED_SECRETS.add(value)


def _secret_values() -> list[str]:
    from_env = [v for k, v in os.environ.items() if CREDENTIAL_NAME.search(k) and v and len(v) >= 8]
    return from_env + sorted(_REGISTERED_SECRETS)


def validate(event: dict) -> None:
    unknown = set(event) - set(CALLER_FIELDS)
    if unknown:
        raise AuditTrailError(f"Unknown or writer-only fields: {sorted(unknown)}")
    if event.get("event_type") not in EVENT_TYPES:
        raise AuditTrailError(f"Unknown event_type {event.get('event_type')!r}")
    kind, actor, mode = event.get("actor_kind"), event.get("actor"), event.get("mode")
    if kind not in ACTOR_KINDS:
        raise AuditTrailError(f"Unknown actor_kind {kind!r}")
    if kind == "agent" and (actor not in AGENTS or mode not in MODES or not event.get("run_id")):
        raise AuditTrailError("Agent events need a known agent, a mode (live/simulated) and a run_id")
    if kind == "system" and actor != "system":
        raise AuditTrailError("System events must have actor 'system'")
    if kind == "human" and not (isinstance(actor, str) and actor.startswith("human:")):
        raise AuditTrailError("Human events must have actor 'human:<name>'")
    if mode not in (None, *MODES):
        raise AuditTrailError(f"Unknown mode {mode!r}")
    outcome = event.get("outcome")
    if outcome is not None and (not isinstance(outcome, dict) or outcome.get("status") not in OUTCOMES):
        raise AuditTrailError(f"outcome must be a dict with status in {OUTCOMES}")
    try:
        text = canonical(event)
    except (TypeError, ValueError) as exc:
        raise AuditTrailError(f"Event is not JSON-serialisable: {exc}") from exc
    if any(secret in text for secret in _secret_values()):
        raise AuditTrailError("Event contains the value of a credential environment variable; refusing to record it")


def verify_events(events) -> dict:
    """Recompute the chain. Reports problems; never repairs."""
    problems: list[str] = []
    if not isinstance(events, list):
        return {"events": 0, "valid": False, "problems": ["trail is not a JSON array"]}
    prev, seen, per_run = GENESIS, set(), {}
    for i, e in enumerate(events, start=1):
        if not isinstance(e, dict):
            problems.append(f"entry {i} is not an object")
            continue
        if e.get("seq") != i:
            problems.append(f"entry {i}: seq is {e.get('seq')}, expected {i}")
        if e.get("prev_hash") != prev:
            problems.append(f"entry {i}: prev_hash does not link to the previous event")
        if event_hash(e) != e.get("hash"):
            problems.append(f"entry {i}: content does not match its hash")
        if e.get("event_id") in seen:
            problems.append(f"entry {i}: duplicate event_id {e.get('event_id')}")
        seen.add(e.get("event_id"))
        if e.get("run_id"):
            per_run[e["run_id"]] = per_run.get(e["run_id"], 0) + 1
            if e.get("run_seq") != per_run[e["run_id"]]:
                problems.append(f"entry {i}: run_seq is {e.get('run_seq')}, expected {per_run[e['run_id']]}")
        prev = e.get("hash")
    return {"events": len(events), "valid": not problems, "problems": problems}


class AuditTrail:
    """Append-preserving writer for one audit_trail.json file."""

    def __init__(self, path: Path, lock_timeout: float = 15.0):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.lock_timeout = lock_timeout

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            events = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuditTrailCorrupt(f"{self.path.name} is not readable JSON: {exc}") from exc
        report = verify_events(events)
        if not report["valid"]:
            raise AuditTrailCorrupt(f"{self.path.name} failed verification: {report['problems'][:3]}")
        return events

    def verify(self) -> dict:
        if not self.path.exists():
            return {"events": 0, "valid": True, "problems": [], "exists": False}
        try:
            events = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"events": 0, "valid": False, "problems": [f"not readable JSON: {exc}"], "exists": True}
        return {**verify_events(events), "exists": True}

    def append(self, new_events: list[dict]) -> list[dict]:
        """Append events atomically (all or none). Returns the stored events.
        An event whose event_id is already stored with identical content is a retry and is not
        stored twice; the same event_id with different content raises DuplicateEventConflict."""
        return self.append_with(lambda _existing: new_events)

    def append_with(self, build) -> list[dict]:
        """Like append, but build(existing_events) computes the new events WHILE the lock is held,
        so decisions based on the current trail (e.g. reconciliation) cannot race."""
        try:
            with locks.file_lock(self.lock_path, self.lock_timeout):
                events = self.read()
                new_events = list(build(events))
                for e in new_events:
                    validate(e)
                for stale in self.path.parent.glob(self.path.name + ".tmp-*"):
                    stale.unlink(missing_ok=True)            # leftovers of interrupted writers (we hold the lock)
                by_id = {e["event_id"]: e for e in events}
                per_run: dict[str, int] = {}
                for e in events:
                    if e.get("run_id"):
                        per_run[e["run_id"]] = per_run.get(e["run_id"], 0) + 1
                stored, added = [], []
                for e in new_events:
                    existing = by_id.get(e["event_id"])
                    if existing is not None:
                        if any(existing.get(k) != e.get(k) for k in CALLER_FIELDS):
                            raise DuplicateEventConflict(f"event_id {e['event_id']} already stored with different content")
                        stored.append(existing)
                        continue
                    full = {k: e.get(k) for k in CALLER_FIELDS}
                    full["seq"] = len(events) + len(added) + 1
                    if full["run_id"]:
                        per_run[full["run_id"]] = per_run.get(full["run_id"], 0) + 1
                    full["run_seq"] = per_run.get(full["run_id"]) if full["run_id"] else None
                    full["prev_hash"] = (added[-1] if added else events[-1] if events else {"hash": GENESIS})["hash"]
                    full["hash"] = event_hash(full)
                    by_id[full["event_id"]] = full
                    added.append(full)
                    stored.append(full)
                if added:
                    self._replace(events + added)
                return stored
        except locks.LockTimeout as exc:
            raise AuditTrailError(f"Could not lock {self.path.name}: {exc}") from exc
        except OSError as exc:
            raise AuditTrailError(f"Could not write {self.path.name}: {exc}") from exc

    def _swap(self, tmp: Path, attempts: int = 40, pause: float = 0.05) -> None:
        """os.replace, retried briefly on PermissionError. PRECAUTION: on Windows another program
        (antivirus, search indexer) can hold a file open for a moment, which makes a rename fail with
        'Permission denied'. This was NOT observed in our tests (the permission errors seen on 2026-10-09
        came from the lock-file bug fixed in locks.py). We still hold the trail lock, so retrying is safe;
        after about 2 seconds the error is raised (fail-closed)."""
        for attempt in range(attempts):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(pause)

    def _replace(self, events: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        data = json.dumps(events, indent=2, ensure_ascii=False) + "\n"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            self._swap(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        if os.name != "nt":                                   # make the rename itself durable on POSIX
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)


# ---------------------------------------------------------------- run recording

def _error_code(text: str) -> str:
    m = re.match(r"([A-Z_]+):", text or "")
    return m.group(1) if m else "TOOL_ERROR"


def summarize(payload, limit: int = 300) -> str:
    text = payload if isinstance(payload, str) else canonical(payload)
    return text if len(text) <= limit else text[: limit - 3] + "..."


class RunRecorder:
    """Records one run. Identity (agent names, run_id, mode) comes from the trusted launcher, never
    from a model. Used as a context manager it holds the run lock, so reset/snapshot refuse meanwhile."""

    def __init__(self, trail: AuditTrail, run_id: str, mode: str, *, active_dir: Path | None = None,
                 business_date: str | None = None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        uuid.UUID(run_id)
        self.trail, self.run_id, self.mode = trail, run_id, mode
        self.active_dir, self.business_date = active_dir, business_date
        self._lock_cm = None

    def __enter__(self):
        if self.active_dir is not None:
            self._lock_cm = locks.file_lock(locks.run_lock_path(self.active_dir), timeout=0)
            self._lock_cm.__enter__()
        return self

    def __exit__(self, *exc):
        if self._lock_cm is not None:
            self._lock_cm.__exit__(*exc)
            self._lock_cm = None
        return False

    def _event(self, event_type: str, actor: str, actor_kind: str = "agent", **fields) -> dict:
        return make_event(event_type, actor=actor, actor_kind=actor_kind, mode=self.mode, run_id=self.run_id,
                          business_date=self.business_date, **fields)

    def record(self, event_type: str, actor: str, actor_kind: str = "agent", **fields) -> dict:
        return self.trail.append([self._event(event_type, actor, actor_kind, **fields)])[0]

    def run_started(self, *, model: str, details: dict | None = None) -> dict:
        return self.record("run_started", "system", "system", details={"model": model, **(details or {})})

    def run_finished(self, *, status: str, usage: dict | None = None, details: dict | None = None) -> dict:
        return self.record("run_finished", "system", "system", usage=usage,
                           outcome={"status": "ok" if status == "completed" else "error", "run_status": status},
                           details=details)

    async def tool_call(self, *, agent: str, chain: list[str], tool: str, args: dict, invoke, ticket_id=None):
        """Audit one MCP tool call around `invoke(call_id)`, which must return (is_error, payload).
        Fail-closed: if the 'started' event cannot be saved, the tool is NOT called. If the call ran
        but 'finished' cannot be saved, AuditTrailError is raised and reconciliation reports the gap."""
        call_id = str(uuid.uuid4())
        self.record("tool_call_started", agent, chain=list(chain), tool=tool, args=args, call_id=call_id,
                    ticket_id=ticket_id)
        try:
            is_error, payload = await invoke(call_id)
        except Exception as exc:                              # transport or client failure
            self.record("tool_call_finished", agent, chain=list(chain), tool=tool, call_id=call_id, ticket_id=ticket_id,
                        outcome={"status": "error", "code": "CALL_FAILED", "message": summarize(str(exc))})
            raise
        except BaseException:                                 # cancelled (e.g. wall-clock limit); outcome unknown
            self.record("tool_call_finished", agent, chain=list(chain), tool=tool, call_id=call_id, ticket_id=ticket_id,
                        outcome={"status": "incomplete", "code": "CANCELLED",
                                 "message": "Call cancelled before its result was received; any database change is "
                                            "visible via reconciliation, not assumed."})
            raise
        if is_error:
            code = _error_code(payload if isinstance(payload, str) else "")
            status = "refused" if code in ("NOT_PERMITTED", "NO_RUN_CONTEXT", "REFUSED_RULE", "DUPLICATE",
                                           "DB_DENIED", "INVALID_INPUT", "NOT_FOUND") else "error"
            self.record("tool_call_finished", agent, chain=list(chain), tool=tool, call_id=call_id, ticket_id=ticket_id,
                        outcome={"status": status, "code": code, "message": summarize(payload)})
        else:
            links = payload.get("links") if isinstance(payload, dict) else None
            self.record("tool_call_finished", agent, chain=list(chain), tool=tool, call_id=call_id, ticket_id=ticket_id,
                        outcome={"status": "ok"}, result_summary=summarize(payload),
                        result_sha256=sha256_text(canonical(payload)), links=links)
        return is_error, payload


def system_event(event_type: str, *, outcome: dict, details: dict | None = None) -> dict:
    """A system (non-agent) event such as db_reset or db_snapshot."""
    return make_event(event_type, actor="system", actor_kind="system", outcome=outcome, details=details)
