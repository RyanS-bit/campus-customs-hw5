"""Full agent responses for one run (Problem 9, follow-up 20).

The audit trail keeps each agent's reply only as a 300-character summary plus a SHA-256 of the full
reply (agent_finished.result_sha256). This module keeps the FULL reply, untruncated, in a separate
per-run evidence file, without changing the trail's events or summaries:

    output/run_responses/<run_id>.jsonl      one JSON record per line, append-only, hash-chained

  * The file is created exclusively ('x') when the run starts, so an existing file is never overwritten.
  * Records are written by trusted code only (delegation.run_agent), never by a model.
  * Each response record names the agent, its role (entry / specialist), the delegation chain, the
    delegation_id that started it, the full task, and the full reply with its SHA-256. It also carries
    the event_id of the matching agent_finished trail event, which is written right after it and
    carries the record's sequence number back (details.response_record). Either side can be checked.
  * Runs recorded before this change have no file. Nothing is reconstructed for them: their replies
    exist only as the trail's summaries, and reconcile() says so.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import audit_trail

DIR_NAME = "run_responses"
FORMAT = "cc-run-responses/1"
KINDS = ("responses_opened", "agent_response", "agent_response_unavailable", "agent_response_withheld")
NOT_RECORDED = ("This run was recorded before full-response recording existed. Its agent replies exist only as "
                "the audit trail's 300-character summaries (result_summary); they are not complete transcripts "
                "and have not been reconstructed.")


class ResponseLogError(audit_trail.AuditTrailError):
    """The response file could not be written or read. Callers must fail closed (treated like a trail failure)."""


def responses_dir(trail_path: Path) -> Path:
    return Path(trail_path).parent / DIR_NAME


def responses_path(trail_path: Path, run_id: str) -> Path:
    return responses_dir(trail_path) / f"{run_id}.jsonl"


def _record_hash(record: dict) -> str:
    return audit_trail.sha256_text(audit_trail.canonical({k: v for k, v in record.items() if k != "hash"}))


def _contains_secret(obj) -> bool:
    text = audit_trail.canonical(obj)
    return any(secret in text for secret in audit_trail._secret_values())


class ResponseLog:
    """Writer for one run's response file. One run at a time holds the run lock; within the run,
    sibling delegations may finish concurrently, so appends are serialised by a lock."""

    def __init__(self, path: Path, run_id: str, mode: str):
        self.path, self.run_id, self.mode = Path(path), run_id, mode
        self._lock = threading.Lock()
        self._seq, self._prev = 0, audit_trail.GENESIS

    def open(self, *, run_started_event_id: str | None, ticket_id: int | None) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.path, "x", encoding="utf-8"):     # exclusive: refuse to overwrite an existing file
                pass
        except FileExistsError as exc:
            raise ResponseLogError(f"{self.path.name} already exists; refusing to overwrite it") from exc
        except OSError as exc:
            raise ResponseLogError(f"cannot create {self.path.name}: {exc}") from exc
        return self._append({"kind": "responses_opened", "format": FORMAT, "mode": self.mode, "ticket_id": ticket_id,
                             "run_started_event_id": run_started_event_id})

    def _append(self, fields: dict) -> dict:
        with self._lock:
            record = {"record_seq": self._seq + 1, "run_id": self.run_id, "recorded_utc": audit_trail.utc_now(),
                      **fields, "prev_hash": self._prev}
            record["hash"] = _record_hash(record)
            try:
                line = json.dumps(record, ensure_ascii=False, sort_keys=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
            except (OSError, TypeError, ValueError) as exc:
                raise ResponseLogError(f"cannot append to {self.path.name}: {exc}") from exc
            self._seq, self._prev = record["record_seq"], record["hash"]
            return record

    def agent_response(self, *, agent: str, chain: list[str], delegation_id: str | None, task: str, response: dict,
                       trail_event_id: str) -> dict:
        """Store the agent's complete reply. response_sha256 equals the trail's agent_finished.result_sha256."""
        sha = audit_trail.sha256_text(audit_trail.canonical(response))
        common = {"agent": agent, "role": "entry" if len(chain) == 1 else "specialist", "chain": list(chain),
                  "delegated_by": chain[-2] if len(chain) > 1 else None, "delegation_id": delegation_id,
                  "trail_event_id": trail_event_id, "response_sha256": sha}
        if _contains_secret({"task": task, "response": response}):
            return self._append({"kind": "agent_response_withheld", **common, "task": None, "response": None,
                                 "reason": "the reply or task contained a credential value; the text is withheld"})
        return self._append({"kind": "agent_response", **common, "task": task, "response": response})

    def unavailable(self, *, agent: str, chain: list[str], delegation_id: str | None, task: str, error_code: str,
                    trail_event_id: str) -> dict:
        """The agent ended without a reply (error, limit, cancellation). Recorded so the gap is explicit."""
        return self._append({"kind": "agent_response_unavailable", "agent": agent,
                             "role": "entry" if len(chain) == 1 else "specialist", "chain": list(chain),
                             "delegated_by": chain[-2] if len(chain) > 1 else None, "delegation_id": delegation_id,
                             "trail_event_id": trail_event_id, "task": None if _contains_secret(task) else task,
                             "error_code": error_code, "response": None, "response_sha256": None})


def read(path: Path) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError) as exc:
        raise ResponseLogError(f"cannot read {Path(path).name}: {exc}") from exc


def reconcile(trail_events: list[dict], run_id: str, path: Path) -> dict:
    """Check one run's response file against the audit trail. Read-only; reports, never repairs."""
    run_events = [e for e in trail_events if e.get("run_id") == run_id]
    finished = [e for e in run_events if e.get("event_type") == "agent_finished"]
    started = next((e for e in run_events if e.get("event_type") == "run_started"), None)
    if not Path(path).exists():
        expected = any((e.get("details") or {}).get("response_record") for e in finished)
        return {"run_id": run_id, "recorded": False, "consistent": not expected, "records": 0,
                "problems": ["response file missing although the trail references it"] if expected else [],
                "note": NOT_RECORDED}

    problems: list[str] = []
    records = read(path)
    prev = audit_trail.GENESIS
    for i, r in enumerate(records, 1):
        if r.get("record_seq") != i:
            problems.append(f"record {i}: record_seq {r.get('record_seq')} out of order")
        if r.get("run_id") != run_id:
            problems.append(f"record {i}: run_id {r.get('run_id')} does not match")
        if r.get("kind") not in KINDS:
            problems.append(f"record {i}: unknown kind {r.get('kind')!r}")
        if r.get("prev_hash") != prev or r.get("hash") != _record_hash(r):
            problems.append(f"record {i}: hash chain broken")
        prev = r.get("hash")
        if r.get("kind") == "agent_response" and r.get("response_sha256") != audit_trail.sha256_text(
                audit_trail.canonical(r.get("response"))):
            problems.append(f"record {i}: response does not match its response_sha256")
    if not records or records[0].get("kind") != "responses_opened":
        problems.append("first record is not responses_opened")
    elif started is not None and records[0].get("run_started_event_id") != started.get("event_id"):
        problems.append("responses_opened does not reference this run's run_started event")

    by_event = {e.get("event_id"): e for e in finished}
    delegations = {(e.get("details") or {}).get("delegation_id"): e for e in run_events
                   if e.get("event_type") == "delegation_requested" and (e.get("details") or {}).get("delegation_id")}
    matched = set()
    for r in records[1:]:
        ev = by_event.get(r.get("trail_event_id"))
        if ev is None:
            problems.append(f"record {r.get('record_seq')}: no agent_finished event {r.get('trail_event_id')}")
            continue
        matched.add(ev["event_id"])
        if ev.get("actor") != r.get("agent") or list(ev.get("chain") or []) != r.get("chain"):
            problems.append(f"record {r.get('record_seq')}: agent or chain differs from the trail event")
        ref = (ev.get("details") or {}).get("response_record") or {}
        if ref.get("record_seq") != r.get("record_seq"):
            problems.append(f"record {r.get('record_seq')}: trail event points to record {ref.get('record_seq')}")
        if r.get("kind") in ("agent_response", "agent_response_withheld") and ev.get("result_sha256") != r.get("response_sha256"):
            problems.append(f"record {r.get('record_seq')}: response_sha256 differs from the trail's result_sha256")
        did = r.get("delegation_id")
        if r.get("role") == "specialist":
            d = delegations.get(did)
            if d is None or (d.get("details") or {}).get("to") != r.get("agent"):
                problems.append(f"record {r.get('record_seq')}: delegation_id {did} has no matching delegation_requested")
        elif did is not None:
            problems.append(f"record {r.get('record_seq')}: entry agent should have no delegation_id")
    for e in finished:
        if (e.get("details") or {}).get("response_record") and e["event_id"] not in matched:
            problems.append(f"agent_finished {e['event_id']} references a response record that is missing")
    return {"run_id": run_id, "recorded": True, "consistent": not problems, "records": len(records),
            "responses": sum(r.get("kind") == "agent_response" for r in records),
            "unavailable": sum(r.get("kind") == "agent_response_unavailable" for r in records),
            "withheld": sum(r.get("kind") == "agent_response_withheld" for r in records),
            "problems": problems}
