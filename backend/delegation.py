"""All-to-all delegation between the five agents (Problem 5, Stage 5; architecture section 15).

Every agent has the same `delegate(to, task)` tool. Safeguards are enforced HERE, in code:
  * cycle prevention   `to` may not already be in the active chain (refusal is a normal tool result)
  * depth backstop     at most RunBudget.max_depth hops
  * delegation budget  RunBudget.max_delegations per run (BudgetExceeded stops the run)
  * shared usage       the child run receives the parent's usage object and the same UsageLimits,
                       so all nested model requests count against ONE limit
Identity is never taken from the model: the child's chain is derived from the trusted parent chain,
and each agent's MCP toolset is bound to that agent's own launch identity (runner.py).
Follow-up 20: each delegation gets a delegation_id (trusted code, uuid4) carried on its trail events and on
the child's agent events; each agent's FULL reply is stored in the per-run response file (run_responses.py)
before its agent_finished event, which keeps its 300-character summary and points back to the record.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace

from pydantic_ai import RunContext
from pydantic_ai.exceptions import UsageLimitExceeded

import audit_trail
import run_responses
from models import AgentName, AgentReport, BudgetExceeded, RunDeps

STOP_RUN = (UsageLimitExceeded, BudgetExceeded, audit_trail.AuditTrailError)


async def run_agent(name: str, task: str, deps: RunDeps, usage=None):
    """Run one agent inside the shared run, recording its start and finish."""
    rec, chain, did = deps.recorder, list(deps.chain), deps.delegation_id
    rec.record("agent_started", name, chain=chain, details={"task": audit_trail.summarize(task), "delegation_id": did})
    try:
        result = await deps.team[name].run(task, deps=deps, usage=usage, usage_limits=deps.budget.usage_limits,
                                           model_settings=deps.budget.model_settings)
    except STOP_RUN as exc:
        if not isinstance(exc, audit_trail.AuditTrailError):
            _finished_without_reply(deps, name, task, exc)
        raise
    except Exception as exc:
        _finished_without_reply(deps, name, task, exc)
        raise
    report: AgentReport = result.output
    full = report.model_dump()
    event_id = str(uuid.uuid4())
    details = {"agent_reported_proposals": report.proposals_created, "agent_reported_drafts": report.drafts_created,
               "note": "self-reported by the agent; verified IDs are on tool_call_finished events",
               "delegation_id": did}
    if deps.responses is not None:          # full reply first; the trail event then points to it
        r = deps.responses.agent_response(agent=name, chain=chain, delegation_id=did, task=task, response=full,
                                          trail_event_id=event_id)
        details["response_record"] = _ref(deps.responses, r)
    # The report is the agent's own CLAIM. Verified links live only on tool_call_finished events.
    rec.record("agent_finished", name, chain=chain, outcome={"status": "ok"}, event_id=event_id,
               result_summary=audit_trail.summarize(full),
               result_sha256=audit_trail.sha256_text(audit_trail.canonical(full)), details=details)
    return result


def _ref(log, record: dict) -> dict:
    return {"file": f"{run_responses.DIR_NAME}/{log.path.name}", "record_seq": record["record_seq"],
            "kind": record["kind"], "record_hash": record["hash"]}


def _finished_without_reply(deps: RunDeps, name: str, task: str, exc: BaseException) -> None:
    """agent_finished (error) for an agent that produced no reply; the response file records the gap explicitly."""
    event_id, chain, did = str(uuid.uuid4()), list(deps.chain), deps.delegation_id
    details = {"delegation_id": did}
    if deps.responses is not None:
        r = deps.responses.unavailable(agent=name, chain=chain, delegation_id=did, task=task,
                                       error_code=type(exc).__name__, trail_event_id=event_id)
        details["response_record"] = _ref(deps.responses, r)
    deps.recorder.record("agent_finished", name, chain=chain, event_id=event_id, details=details,
                         outcome={"status": "error", "code": type(exc).__name__, "message": audit_trail.summarize(str(exc))})


async def delegate(ctx: RunContext[RunDeps], to: AgentName, task: str) -> str:
    """Ask another agent (boss, inventory, accounting, facilities or customer_service) to do a task
    within its own authority. Returns that agent's report. You cannot delegate to an agent already
    working on the current chain."""
    deps = ctx.deps
    me, chain, rec = deps.current, deps.chain, deps.recorder
    delegation_id = str(uuid.uuid4())                     # trusted code, never the model
    rec.record("delegation_requested", me, chain=list(chain),
               details={"to": to, "task": audit_trail.summarize(task), "delegation_id": delegation_id})

    if to in chain:
        reason = f"Refused: {to} is already working on this chain ({' -> '.join(chain)}). Answer with what you have."
        rec.record("delegation_refused", me, chain=list(chain), outcome={"status": "refused", "code": "CYCLE", "message": reason},
                   details={"to": to, "delegation_id": delegation_id})
        return reason
    if len(chain) >= deps.budget.max_depth + 1:
        reason = f"Refused: delegation depth limit ({deps.budget.max_depth} hops) reached. Answer with what you have."
        rec.record("delegation_refused", me, chain=list(chain), outcome={"status": "refused", "code": "DEPTH", "message": reason},
                   details={"to": to, "delegation_id": delegation_id})
        return reason
    try:
        deps.budget.count_delegation()
    except BudgetExceeded as exc:
        rec.record("budget_exceeded", me, chain=list(chain), outcome={"status": "error", "code": "DELEGATION_BUDGET",
                                                                      "message": str(exc)}, details={"to": to, "delegation_id": delegation_id})
        raise

    child = replace(deps, chain=chain + (to,), delegation_id=delegation_id)
    try:
        result = await run_agent(to, task, child, usage=ctx.usage)
    except STOP_RUN:
        raise
    except Exception as exc:                       # the child failed; tell the parent instead of crashing the run
        message = f"Delegation to {to} failed: {type(exc).__name__}: {audit_trail.summarize(str(exc), 200)}"
        rec.record("delegation_completed", me, chain=list(chain), outcome={"status": "error", "code": type(exc).__name__,
                                                                           "message": message}, details={"to": to, "delegation_id": delegation_id})
        return message
    rec.record("delegation_completed", me, chain=list(chain), outcome={"status": "ok"}, details={"to": to, "delegation_id": delegation_id})
    return json.dumps({"from": to, "report": result.output.model_dump()})
