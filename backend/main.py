"""Campus Customs backend (Problem 7): FastAPI routes between the dashboard (Problem 8) and the existing
five-agent team, MCP tools, working database and audit trail (Problems 3-5).

Start it from hw5/backend/ (the professor's command):
    uvicorn main:app --reload --port 8000
Then open http://localhost:8000 (interactive API documentation: http://localhost:8000/docs).

This file is the HTTP layer only. It contains no SQL and no business rules:
  * reads              mcp_server/desk_reads.py   (read-only SQLite connections)
  * agent runs         backend/runner.py run_task  (the Problem 5 runner, unchanged rules and budgets)
  * agent activity     output/audit_trail.json     (verified hash chain; never fabricated)
  * full agent replies output/run_responses/<run_id>.jsonl via mcp_server/run_responses.py (follow-up 20; read-only here)
  * human decisions    mcp_server/approval.py      (the ONE approval/execution path; atomic; audited)
  * reset              mcp_server/approval.py controlled_reset -> maintenance.py (the existing verified reset, R8)
  * audit status       mcp_server/audit_reconcile.py check (read-only; gaps are reconciled before the next change)

Safety boundaries (details in output/harness.md section 11.7):
  * Agents cannot reach these routes: they have no HTTP, shell or code tools, only their MCP tools.
  * Every POST needs the header  X-Human-Decision: confirm  (blocks accidental and cross-site requests;
    it is NOT authentication). Decisions record a typed name for attribution only (local prototype).
  * Live agent runs are possible only if the person starting the server sets CC_LIVE_AUTHORIZATION;
    otherwise POST /api/tickets/{id}/run refuses (403). Nothing here ever chooses a model.
  * One mutating operation at a time: runs, decisions and resets share the run lock (and an in-process gate).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

HERE = Path(__file__).resolve().parent
HW5 = HERE.parent
for _d in (HW5 / "mcp_server", HERE):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from fastapi import Depends, FastAPI, Header, Query, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field, StrictBool  # noqa: E402

import approval  # noqa: E402
import audit_reconcile  # noqa: E402
import audit_trail  # noqa: E402
import desk_reads  # noqa: E402
import locks  # noqa: E402
import paths as paths_mod  # noqa: E402
import run_responses  # noqa: E402
import runner  # noqa: E402

HUMAN_HEADER = "X-Human-Decision"
HUMAN_HEADER_VALUE = "confirm"
DASHBOARD_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]     # Vite dev server (Problem 8)
LIVE_AUTH_VAR = "CC_LIVE_AUTHORIZATION"
EVENTS_DEFAULT_LIMIT, EVENTS_MAX_LIMIT = 200, 1000

RunFactory = Callable[[int, str], Awaitable["runner.RunOutcome"]]


# ---------------------------------------------------------------- errors

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


DECISION_STATUS = {"NOT_FOUND": 404, "NOT_PENDING": 409, "REFUSED_RULE": 409, "BUSY": 409, "INVALID_INPUT": 422,
                   "DB_UNAVAILABLE": 503, "AUDIT_FAILED": 503, "RESET_FAILED": 500}


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---------------------------------------------------------------- request models

class DecisionRequest(BaseModel):
    """A human decision. decided_by is recorded for ATTRIBUTION ONLY; it is not authenticated."""
    model_config = ConfigDict(extra="forbid")
    decided_by: str = Field(min_length=2, max_length=80, description="Name of the person deciding (attribution only)")
    confirm: Literal[True] = Field(description="Must be true: the person confirms this decision")
    note: str | None = Field(default=None, max_length=500)


class ResolveRequest(BaseModel):
    """Resolving a ticket records the person's statement; nothing is inferred from approvals. Two bases (follow-up 36):
    business_outcome            the underlying business issue is actually solved (business_outcome_confirmed = true)
    simulated_workflow_complete the desk's workflow in this simulation is complete; fulfilment is NOT verified
                                (workflow_complete_confirmed = true, business_outcome_confirmed = false, and at least
                                one unverified outcome). The combinations are checked in mcp_server/approval.py."""
    model_config = ConfigDict(extra="forbid")
    decided_by: str = Field(min_length=2, max_length=80, description="Name of the person deciding (attribution only)")
    confirm: Literal[True] = Field(description="Must be true: the person confirms this decision")
    resolution_basis: Literal["business_outcome", "simulated_workflow_complete"] = Field(
        default="business_outcome", description="What the person is confirming")
    business_outcome_confirmed: StrictBool = Field(
        default=False, description="business_outcome: must be true (the underlying business issue is resolved; not verified "
                                   "by the system). simulated_workflow_complete: must be false")
    workflow_complete_confirmed: StrictBool = Field(
        default=False, description="simulated_workflow_complete: must be true (the desk's simulated workflow is complete)")
    unverified_outcomes: list[Literal["delivery", "customer_acceptance", "message_sent", "outside_arrangement"]] = Field(
        default_factory=list, max_length=4, description="simulated_workflow_complete: what remains unverified (at least one)")
    resolution_summary: str = Field(min_length=10, max_length=1000,
                                    description="In the person's own words: what was done, and (for the simulated basis) "
                                                "what remains unverified")
    note: str | None = Field(default=None, max_length=500)


class ExecutePurchaseRequest(BaseModel):
    """Spend cash on an APPROVED purchase (Problem 9, F3). The amount, its basis, and the supplier's agreement are the
    person's explicit statements; the system cannot contact suppliers (R7) and records them as such."""
    model_config = ConfigDict(extra="forbid")
    decided_by: str = Field(min_length=2, max_length=80, description="Name of the person executing (attribution only)")
    confirm: Literal[True] = Field(description="Must be true: the person confirms this expenditure")
    amount_basis: Literal["recorded_unit_cost_estimate", "supplier_confirmed_price"] = Field(
        description="Database cost estimate (quantity × stored unit cost) or a price the supplier confirmed outside the system")
    amount: float = Field(gt=0, description="The actual amount to pay now, confirmed by the person")
    amount_confirmed: Literal[True] = Field(description="Must be true: the person confirms this is the actual amount")
    supplier_confirmed: Literal[True] = Field(
        description="Must be true: the person states the supplier agreed to supply this item and quantity (not verified)")
    confirmation_note: str | None = Field(default=None, max_length=500,
                                          description="Required for supplier_confirmed_price: how and when the supplier confirmed")
    note: str | None = Field(default=None, max_length=500)


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_by: str = Field(min_length=2, max_length=80, description="Name of the person resetting (attribution only)")
    confirm: Literal["RESET"] = Field(description='Must be the text "RESET"')


# ---------------------------------------------------------------- response models

class TicketSummary(BaseModel):
    id: int
    type: str
    requester: str
    subject: str
    status: str
    is_open: bool
    is_resolved: bool
    sku: str | None = None
    size: str | None = None
    qty: int | None = None
    invoice_id: int | None = None
    lease_id: int | None = None
    notes: str | None = None
    created_at: str
    proposals: dict[str, int]
    awaiting_human_decision: int
    drafts: int
    latest_run_id: str | None = None
    latest_run_phase: str | None = None
    resolution: dict[str, Any] | None = None      # follow-up 36: basis, unverified outcomes, who, when (from audit.db)


class TicketsResponse(BaseModel):
    business_date: str
    open_count: int
    resolved_count: int
    tickets: list[TicketSummary]


class TicketDetailResponse(BaseModel):
    business_date: str
    ticket: dict[str, Any]
    pending_actions: list[dict[str, Any]]
    drafts: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    inventory: dict[str, Any] | None = None     # Problem 8: stored stock and pricing for the ticket's product


class RunAccepted(BaseModel):
    run_id: str
    ticket_id: int
    phase: Literal["queued"]
    mode: str
    status_url: str
    events_url: str
    note: str


class RunView(BaseModel):
    run_id: str
    ticket_id: int | None
    mode: str | None
    phase: Literal["queued", "active", "completed", "stopped", "failed", "interrupted"]
    outcome: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    awaiting_human_action: bool
    proposals_awaiting_decision: list[dict[str, Any]]
    proposals_decided: list[dict[str, Any]]
    drafts_unsent: list[dict[str, Any]]
    ticket_status: str | None
    ticket_resolved: bool
    usage: dict[str, Any] | None
    agent_report: dict[str, Any] | None
    agent_report_source: str | None
    events: int
    events_url: str


class EventView(BaseModel):
    seq: int
    run_seq: int | None
    event_id: str
    ts_utc: str
    event_type: str
    actor: str
    actor_kind: str
    mode: str | None
    run_id: str | None
    ticket_id: int | None
    chain: list[str] | None
    description: str
    tool: str | None
    args: dict[str, Any] | None
    call_id: str | None
    outcome: dict[str, Any] | None
    delegated_by: str | None = None
    delegated_to: str | None = None
    task: str | None = None
    said: str | None = None
    said_summary: str | None = None
    result_summary: str | None = None
    links: dict[str, Any] | None
    usage: dict[str, Any] | None
    details: dict[str, Any] | None


class EventsResponse(BaseModel):
    events: list[EventView]
    next_after_seq: int
    has_more: bool
    trail_events: int
    trail_valid: bool
    source: str


class RunResponsesResponse(BaseModel):
    run_id: str
    recorded: bool
    consistent: bool
    records: list[dict[str, Any]]
    reconciliation: dict[str, Any]
    source: str
    note: str


class ActionsResponse(BaseModel):
    business_date: str
    count: int
    actions: list[dict[str, Any]]
    note: str


class DecisionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    result: str
    decision_id: str
    decided_by: str
    business_date: str
    status: str
    warnings: list[str]
    links: dict[str, Any]
    audit_trail_recorded: bool
    notice: str


class CashResponse(BaseModel):
    business_date: str
    checking_balance: float | None
    checking_as_of: str | None
    source: str
    accounts: list[dict[str, Any]]
    payments_executed: list[dict[str, Any]]
    not_counted_as_cash: dict[str, Any]
    available: dict[str, Any] | None = None      # Problem 9 F3: computed limits; commitments never deducted from cash


class ResetResponse(BaseModel):
    reset: bool
    reset_id: str
    requested_by: str
    result: dict[str, Any]
    audit_trail_recorded: bool
    reconciliation_pending: bool
    audit_db_event_ids: dict[str, int | None]
    earlier_gaps_reconciled: int
    warnings: list[str]


class AuditStatusResponse(BaseModel):
    consistent: bool
    run_active: bool
    json_trail: dict[str, Any]
    audit_db: dict[str, Any]
    incomplete: list[dict[str, Any]]
    cross_reference_problems: list[str]
    note: str


# ---------------------------------------------------------------- run registry (this process only)

@dataclass
class RunState:
    run_id: str
    ticket_id: int
    mode: str
    status: str = "queued"              # queued | running | finished | error
    accepted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    outcome: Any = None
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


def ticket_task(ticket_id: int) -> str:
    return (f"Handle desk ticket {ticket_id} only. Read it with get_ticket({ticket_id}), check what is already "
            f"proposed, investigate with your tools, and delegate to the responsible specialists. Report what is "
            f"proposed, what is blocked, and what a human must decide. Do not work on other tickets.")


def live_run_factory(p: paths_mod.Paths, authorization: str) -> RunFactory:
    """LIVE runs (gpt-6-luna through Portkey, the Problem 5 configuration and budget). Built only when the person
    starting the server set CC_LIVE_AUTHORIZATION; the value is recorded in the audit trail as the authorization."""
    import config
    from models import RunBudget

    async def start(ticket_id: int, run_id: str):
        budget = RunBudget()
        model = config.build_live_model(config.load_config(), budget, trace_id=run_id)
        return await runner.run_task(ticket_task(ticket_id), p=p, model=model, mode="live", budget=budget,
                                     live_authorization=authorization, run_id=run_id, ticket_id=ticket_id)
    return start


# ---------------------------------------------------------------- event helpers

def _said_summary(text: str | None) -> str | None:
    try:
        return json.loads(text).get("summary") if text else None
    except (ValueError, AttributeError):
        return None                                       # truncated in the trail; the raw text is in 'said'


def describe(e: dict) -> str:
    """A one-line description built ONLY from the recorded fields (no model text is invented)."""
    t, actor, d, o = e["event_type"], e["actor"], e.get("details") or {}, e.get("outcome") or {}
    status = o.get("status")
    return {
        "run_started": lambda: f"Run started ({e.get('mode')}, entry agent {d.get('entry_agent')})",
        "run_finished": lambda: f"Run finished: {o.get('run_status')}",
        "agent_started": lambda: f"{actor} started work",
        "agent_finished": lambda: f"{actor} finished ({status})",
        "tool_call_started": lambda: f"{actor} called {e.get('tool')}",
        "tool_call_finished": lambda: f"{e.get('tool')} returned to {actor}: {status}" + (f" ({o.get('code')})" if o.get("code") else ""),
        "delegation_requested": lambda: f"{actor} delegated to {d.get('to')}",
        "delegation_completed": lambda: f"{d.get('to')} reported back to {actor}" + ("" if status == "ok" else f" ({status})"),
        "delegation_refused": lambda: f"delegation from {actor} to {d.get('to')} refused ({o.get('code')})",
        "budget_exceeded": lambda: f"limit reached: {o.get('code')}",
        "error": lambda: f"error: {o.get('code')}",
        "db_reset": lambda: f"working database reset ({status})",
        "human_decision_requested": lambda: f"{actor} requested {d.get('operation')}",
        "human_decision_finished": lambda: f"{actor} {d.get('operation')}: {status}" + (f" ({o.get('code')})" if o.get("code") else ""),
    }.get(t, lambda: t.replace("_", " "))()


def event_view(e: dict, run_tickets: dict[str, int | None]) -> dict:
    d = e.get("details") or {}
    view = {k: e.get(k) for k in ("seq", "run_seq", "event_id", "ts_utc", "event_type", "actor", "actor_kind", "mode",
                                  "run_id", "chain", "tool", "args", "call_id", "outcome", "links", "usage")}
    view["ticket_id"] = e.get("ticket_id") or d.get("ticket_id") or run_tickets.get(e.get("run_id"))
    view["details"] = d
    view["description"] = describe(e)
    if e["event_type"].startswith("delegation_"):
        view["delegated_by"], view["delegated_to"] = e["actor"], d.get("to")
    if e["event_type"] == "agent_started":
        view["task"] = d.get("task")
    if e["event_type"] == "agent_finished":
        view["said"] = e.get("result_summary")
        view["said_summary"] = _said_summary(e.get("result_summary"))
    if e["event_type"] == "tool_call_finished":
        view["result_summary"] = e.get("result_summary")
    return view


# ---------------------------------------------------------------- the application

def create_app(p: paths_mod.Paths | None = None, run_factory: RunFactory | None = None, run_mode: str | None = None,
               trail: audit_trail.AuditTrail | None = None) -> FastAPI:
    """p, run_factory and run_mode are injected by the offline tests (temporary databases, scripted offline models).
    Without them: the resolved project paths, and live runs only if CC_LIVE_AUTHORIZATION is set."""
    p = p or paths_mod.resolve()
    if run_factory is None:
        authorization = os.environ.get(LIVE_AUTH_VAR, "").strip()
        if authorization:
            run_factory, run_mode = live_run_factory(p, authorization), "live"
    trail = trail or audit_trail.AuditTrail(p.audit_trail_json)
    runs: dict[str, RunState] = {}
    gate = threading.Lock()                     # one mutating request at a time inside this process

    @asynccontextmanager
    async def lifespan(_app):
        yield
        for state in runs.values():              # on shutdown or reload: stop unfinished runs (status: interrupted)
            if state.task is not None and not state.task.done():
                state.task.cancel()

    app = FastAPI(title="Campus Customs backend", version="1.0.0", lifespan=lifespan,
                  description="Problem 7 routes for the Campus Customs desk. Agents prepare; humans decide. "
                              "LOCAL PROTOTYPE: names are recorded for attribution only, not authenticated.")
    app.add_middleware(CORSMiddleware, allow_origins=DASHBOARD_ORIGINS, allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", HUMAN_HEADER])
    app.state.paths, app.state.runs, app.state.trail = p, runs, trail

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, **exc.extra}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        problems = [{"field": ".".join(str(x) for x in err.get("loc", [])), "message": err.get("msg")} for err in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "INVALID_INPUT", "message": "The request is not valid.",
                                                                "problems": problems}})

    # ------------------------------------------------------------ helpers bound to this app

    def require_human(x_human_decision: str | None = Header(default=None, alias=HUMAN_HEADER)) -> None:
        if x_human_decision != HUMAN_HEADER_VALUE:
            raise ApiError(403, "HUMAN_CONFIRMATION_REQUIRED",
                           f"This action needs the header '{HUMAN_HEADER}: {HUMAN_HEADER_VALUE}' sent by the dashboard "
                           "when a person clicks. It guards against accidental or cross-site requests; it is not authentication.")

    def read(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except desk_reads.ReadError as exc:
            raise ApiError(503, "DB_UNAVAILABLE", str(exc)) from None

    def trail_events() -> list[dict]:
        try:
            return trail.read()
        except audit_trail.AuditTrailError as exc:
            raise ApiError(503, "AUDIT_TRAIL_UNREADABLE", f"The audit trail failed verification and is not served: {exc}") from None

    def run_tickets(events: list[dict]) -> dict[str, int | None]:
        mapping = {e["run_id"]: (e.get("details") or {}).get("ticket_id") for e in events if e["event_type"] == "run_started"}
        mapping.update({rid: s.ticket_id for rid, s in runs.items() if rid not in mapping})
        return mapping

    def busy_run() -> RunState | None:
        return next((s for s in runs.values() if s.status in ("queued", "running")), None)

    def enter_gate():
        if not gate.acquire(blocking=False):
            raise ApiError(409, "BUSY", "Another change is being processed; try again in a moment.")
        active = busy_run()
        if active is not None:
            gate.release()
            raise ApiError(409, "RUN_ACTIVE", f"Agent run {active.run_id} (ticket {active.ticket_id}) is in progress; "
                                              "wait until it finishes.", run_id=active.run_id)

    def run_view(run_id: str, events: list[dict]) -> dict:
        mine = [e for e in events if e.get("run_id") == run_id]
        state = runs.get(run_id)
        if not mine and state is None:
            raise ApiError(404, "NOT_FOUND", f"Unknown run {run_id}")
        started = next((e for e in mine if e["event_type"] == "run_started"), None)
        finished = next((e for e in mine if e["event_type"] == "run_finished"), None)
        ticket_id = (started.get("details") or {}).get("ticket_id") if started else state.ticket_id
        error = state.error if state else None
        if finished:
            phase = {"completed": "completed", "stopped": "stopped"}.get(finished["outcome"].get("run_status"), "failed")
            error = error or (finished.get("details") or {}).get("error")
        elif state and state.status in ("queued", "running"):
            phase = "active" if started else "queued"
        elif state and state.status == "error":
            phase = "failed"
        elif started and locks.run_active(p.active_dir):
            phase = "active"                      # held by another process
        else:
            phase = "interrupted"                  # started, never finished (process stopped); see verify_audit.py
        awaiting = read(desk_reads.list_actions, p, run_id=run_id, status="proposed")
        decided = [a for a in read(desk_reads.list_actions, p, run_id=run_id) if a["status"] != "proposed"]
        drafts = read(desk_reads.drafts_for_run, p, run_id)
        statuses = read(desk_reads.ticket_ids, p)
        ticket_status = statuses.get(ticket_id) if ticket_id is not None else None
        if phase in ("queued", "active"):
            outcome = "in_progress"
        elif phase in ("failed", "interrupted"):
            outcome = phase
        elif ticket_status == "resolved":
            outcome = "ticket_resolved_by_human"
        elif awaiting:
            outcome = "awaiting_human_approval"
        elif drafts:
            outcome = "awaiting_human_review_of_drafts"
        else:
            outcome = "incomplete_limit_reached" if phase == "stopped" else "completed_nothing_proposed"
        report, source = None, None
        if state and state.outcome is not None and state.outcome.report is not None:
            report, source = state.outcome.report.model_dump(), "entry agent's final report (its own claim; verify against events)"
        else:
            entry_done = next((e for e in mine if e["event_type"] == "agent_finished" and len(e.get("chain") or []) == 1), None)
            if entry_done and entry_done.get("result_summary"):
                report = {"recorded_summary": entry_done["result_summary"]}
                source = "audit trail result_summary of the entry agent (may be truncated)"
        return {"run_id": run_id, "ticket_id": ticket_id, "mode": started["mode"] if started else (state.mode if state else None),
                "phase": phase, "outcome": outcome, "started_at": started["ts_utc"] if started else None,
                "finished_at": finished["ts_utc"] if finished else None, "error": error,
                # Follow-up 39: a resolved ticket awaits nothing, even if an unsent historical draft remains.
                "awaiting_human_action": (phase in ("completed", "stopped") and ticket_status != "resolved"
                                          and bool(awaiting or drafts)),
                "proposals_awaiting_decision": awaiting, "proposals_decided": decided, "drafts_unsent": drafts,
                "ticket_status": ticket_status, "ticket_resolved": ticket_status == "resolved",
                "usage": finished.get("usage") if finished else None, "agent_report": report, "agent_report_source": source,
                "events": len(mine), "events_url": f"/api/events?run_id={run_id}"}

    def decide(fn, target_id: int, body: BaseModel, **extra) -> dict:
        enter_gate()
        try:
            return fn(p, trail, target_id, decided_by=body.decided_by, note=body.note, **extra)
        except approval.DecisionError as exc:
            raise ApiError(DECISION_STATUS[exc.code], exc.code, exc.message) from None
        except paths_mod.UnsafeWriteError as exc:
            raise ApiError(500, "UNSAFE_WRITE", str(exc)) from None
        finally:
            gate.release()

    # ------------------------------------------------------------ routes

    errors = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
              503: {"model": ErrorResponse}}

    @app.get("/", tags=["service"])
    def index() -> dict:
        """Service information and the list of routes."""
        return {"service": "Campus Customs backend (Problem 7)", "docs": "/docs", "dashboard_origins": DASHBOARD_ORIGINS,
                "live_runs_enabled": run_factory is not None and run_mode == "live",
                "run_mode": run_mode if run_factory is not None else None,
                "routes": sorted(f"{m} {r.path}" for r in app.routes for m in getattr(r, "methods", ()) if m != "HEAD"
                                 and r.path.startswith("/api"))}

    @app.get("/api/tickets", response_model=TicketsResponse, tags=["tickets"], responses=errors)
    def list_tickets():
        """All tickets from the working database, each marked open or resolved."""
        data = read(desk_reads.list_tickets, p)
        events = trail_events()
        latest: dict[int, str] = {}
        for e in events:
            if e["event_type"] == "run_started" and (e.get("details") or {}).get("ticket_id") is not None:
                latest[e["details"]["ticket_id"]] = e["run_id"]
        for rid, s in runs.items():
            if not any(e.get("run_id") == rid for e in events):
                latest[s.ticket_id] = rid
        for t in data["tickets"]:
            rid = latest.get(t["id"])
            t["latest_run_id"] = rid
            t["latest_run_phase"] = run_view(rid, events)["phase"] if rid else None
        return {"business_date": data["business_date"], "tickets": data["tickets"],
                "open_count": sum(t["is_open"] for t in data["tickets"]),
                "resolved_count": sum(t["is_resolved"] for t in data["tickets"])}

    @app.get("/api/tickets/{ticket_id}", response_model=TicketDetailResponse, tags=["tickets"], responses=errors)
    def get_ticket(ticket_id: int):
        """One ticket with its linked invoice or lease, its proposals (with decisions) and its unsent drafts."""
        data = read(desk_reads.get_ticket, p, ticket_id)
        if data is None:
            raise ApiError(404, "NOT_FOUND", f"Unknown ticket {ticket_id}")
        events = trail_events()
        rids = [rid for rid, t in run_tickets(events).items() if t == ticket_id]
        data["runs"] = [{k: v for k, v in run_view(rid, events).items() if k in ("run_id", "phase", "outcome", "started_at",
                                                                                  "finished_at", "mode")} for rid in rids]
        return data

    @app.post("/api/tickets/{ticket_id}/run", response_model=RunAccepted, status_code=202, tags=["runs"],
              responses={**errors, 403: {"model": ErrorResponse}}, dependencies=[Depends(require_human)])
    async def run_ticket(ticket_id: int):
        """Start the Boss-led agent team on one ticket (in the background). Returns the run ID at once."""
        if run_factory is None:
            raise ApiError(403, "RUNS_NOT_AUTHORIZED",
                           f"Agent runs are not enabled in this server process. Live runs call gpt-6-luna through Portkey "
                           f"and cost money, so they start only if the person launching the server sets {LIVE_AUTH_VAR} "
                           f"to a short authorization note (Problem 9).")
        enter_gate()
        try:
            statuses = read(desk_reads.ticket_ids, p)
            if ticket_id not in statuses:
                raise ApiError(404, "NOT_FOUND", f"Unknown ticket {ticket_id}. Known tickets: {sorted(statuses)}")
            if statuses[ticket_id] != "open":
                raise ApiError(409, "TICKET_NOT_OPEN", f"Ticket {ticket_id} is {statuses[ticket_id]!r}; only open tickets are run.")
            if locks.run_active(p.active_dir):
                raise ApiError(409, "RUN_ACTIVE", "Another process holds the run lock (an agent run, reset or decision).")
            run_id = str(uuid.uuid4())
            state = RunState(run_id=run_id, ticket_id=ticket_id, mode=run_mode or "unknown")
            runs[run_id] = state
        finally:
            gate.release()

        async def background():
            state.status = "running"
            try:
                state.outcome = await run_factory(ticket_id, run_id)
                state.status = "finished"
            except locks.LockTimeout:
                state.status, state.error = "error", "RUN_LOCK_BUSY: another process started a run, reset or decision first"
            except Exception as exc:              # recorded by the runner when possible; never hidden
                state.status, state.error = "error", f"{type(exc).__name__}: {audit_trail.summarize(str(exc), 300)}"

        state.task = asyncio.get_running_loop().create_task(background())
        return {"run_id": run_id, "ticket_id": ticket_id, "phase": "queued", "mode": state.mode,
                "status_url": f"/api/runs/{run_id}", "events_url": f"/api/events?run_id={run_id}",
                "note": "Poll the status and events URLs. A finished run never resolves the ticket or approves anything."}

    @app.get("/api/runs/{run_id}", response_model=RunView, tags=["runs"], responses=errors)
    def get_run(run_id: str):
        """Status and outcome of one run: phase, proposals awaiting a human, drafts, and the ticket's real status."""
        return run_view(run_id, trail_events())

    @app.get("/api/runs/{run_id}/responses", response_model=RunResponsesResponse, tags=["runs"], responses=errors)
    def get_run_responses(run_id: str):
        """Every agent's FULL reply for one run, in order, checked against the audit trail (follow-up 20).
        Runs recorded before this existed return recorded=false: only the trail's summaries exist for them."""
        try:
            canonical_id = str(uuid.UUID(run_id))
        except ValueError:
            raise ApiError(404, "NOT_FOUND", f"Unknown run {run_id}") from None
        events = trail_events()
        if canonical_id != run_id or not any(e.get("run_id") == run_id for e in events):
            raise ApiError(404, "NOT_FOUND", f"Unknown run {run_id}")
        path = run_responses.responses_path(trail.path, run_id)
        try:
            check = run_responses.reconcile(events, run_id, path)
            records = run_responses.read(path) if check["recorded"] else []
        except run_responses.ResponseLogError as exc:
            raise ApiError(503, "RESPONSES_UNREADABLE", str(exc)) from None
        return {"run_id": run_id, "recorded": check["recorded"], "consistent": check["consistent"], "records": records,
                "reconciliation": check, "source": f"output/{run_responses.DIR_NAME}/{run_id}.jsonl",
                "note": ("Complete replies, stored by trusted code; each one's SHA-256 matches its agent_finished event."
                         if check["recorded"] else run_responses.NOT_RECORDED)}

    @app.get("/api/events", response_model=EventsResponse, tags=["events"], responses=errors)
    def get_events(run_id: str | None = None, ticket_id: int | None = None,
                   after_seq: int | None = Query(default=None, ge=0, description="Return events after this seq (polling cursor)"),
                   limit: int = Query(default=EVENTS_DEFAULT_LIMIT, ge=1, le=EVENTS_MAX_LIMIT)):
        """Recorded agent and human activity from output/audit_trail.json, in order (seq)."""
        events = trail_events()
        tickets = run_tickets(events)
        views = [event_view(e, tickets) for e in events]
        if run_id is not None:
            views = [v for v in views if v["run_id"] == run_id]
        if ticket_id is not None:
            views = [v for v in views if v["ticket_id"] == ticket_id]
        last = events[-1]["seq"] if events else 0
        if after_seq is not None:
            views = [v for v in views if v["seq"] > after_seq]
            page, has_more = views[:limit], len(views) > limit
        else:
            page, has_more = views[-limit:], False
        cursor = page[-1]["seq"] if has_more else last
        return {"events": page, "next_after_seq": cursor, "has_more": has_more, "trail_events": len(events),
                "trail_valid": True, "source": "output/audit_trail.json (hash chain verified on every read)"}

    @app.get("/api/actions/pending", response_model=ActionsResponse, tags=["approvals"], responses=errors)
    def pending_actions(ticket_id: int | None = None):
        """Agent proposals that still await a human decision."""
        actions = read(desk_reads.list_actions, p, status="proposed", ticket_id=ticket_id)
        return {"business_date": read(desk_reads.business_date, p), "count": len(actions), "actions": actions,
                "note": "Proposals move no money. Approving a payment executes it; approving a purchase or quote only "
                        "authorizes it (nothing is ordered or sent)."}

    @app.post("/api/actions/{action_id}/approve", response_model=DecisionResponse, tags=["approvals"],
              responses={**errors, 403: {"model": ErrorResponse}}, dependencies=[Depends(require_human)])
    def approve(action_id: int, body: DecisionRequest):
        """Human approval. A payment is executed atomically (cash, invoice or lease, payments row, audit records)."""
        return decide(approval.approve_action, action_id, body)

    @app.post("/api/actions/{action_id}/execute", response_model=DecisionResponse, tags=["approvals"],
              responses={**errors, 403: {"model": ErrorResponse}}, dependencies=[Depends(require_human)])
    def execute_purchase(action_id: int, body: ExecutePurchaseRequest):
        """Human execution of an APPROVED purchase: pays the confirmed amount from checking (one atomic transaction).
        Refused while the vendor has an open invoice, or if cash available for purchases is insufficient. No inventory change."""
        return decide(approval.execute_purchase, action_id, body, amount_basis=body.amount_basis, amount=body.amount,
                      amount_confirmed=body.amount_confirmed, supplier_confirmed=body.supplier_confirmed,
                      confirmation_note=body.confirmation_note)

    @app.post("/api/actions/{action_id}/reject", response_model=DecisionResponse, tags=["approvals"],
              responses={**errors, 403: {"model": ErrorResponse}}, dependencies=[Depends(require_human)])
    def reject(action_id: int, body: DecisionRequest):
        """Human rejection of a proposal. Nothing is executed."""
        return decide(approval.reject_action, action_id, body)

    @app.post("/api/tickets/{ticket_id}/resolve", response_model=DecisionResponse, tags=["tickets"],
              responses={**errors, 403: {"model": ErrorResponse}}, dependencies=[Depends(require_human)])
    def resolve(ticket_id: int, body: ResolveRequest):
        """A person resolves a ticket in their own words, on one of two bases: the business outcome is confirmed, or only
        the simulated workflow is complete with named unverified outcomes (follow-up 36). Recorded as their statement;
        nothing is inferred from approvals. Refused while its proposals still await a decision."""
        return decide(approval.resolve_ticket, ticket_id, body, business_outcome_confirmed=body.business_outcome_confirmed,
                      resolution_summary=body.resolution_summary, resolution_basis=body.resolution_basis,
                      workflow_complete_confirmed=body.workflow_complete_confirmed,
                      unverified_outcomes=list(body.unverified_outcomes))

    @app.get("/api/cash", response_model=CashResponse, tags=["cash"], responses=errors)
    def get_cash():
        """The checking balance as stored in cash_accounts (changes only when a human-approved payment executes)."""
        return read(desk_reads.cash, p)

    @app.post("/api/reset", response_model=ResetResponse, tags=["reset"],
              responses={**errors, 403: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
              dependencies=[Depends(require_human)])
    def reset(body: ResetRequest):
        """Restore the working database from the original (R8). Refused while a run or decision is active.
        Recorded before and after in both audit records; the new copy carries the reset_id (see harness 11.6)."""
        try:
            approval.validate_decider(body.requested_by)
        except approval.DecisionError as exc:
            raise ApiError(422, exc.code, exc.message) from None
        enter_gate()
        try:
            return approval.controlled_reset(p, trail, requested_by=body.requested_by, via="POST /api/reset")
        except approval.DecisionError as exc:
            raise ApiError(DECISION_STATUS[exc.code], exc.code, exc.message) from None
        except paths_mod.UnsafeWriteError as exc:
            raise ApiError(500, "UNSAFE_WRITE", str(exc)) from None
        finally:
            gate.release()

    @app.get("/api/audit", response_model=AuditStatusResponse, tags=["audit"], responses=errors)
    def audit_status():
        """Integrity of both audit records and any operation whose final JSON event is missing (read-only).
        Gaps are reconciled automatically before the next decision or reset."""
        report = audit_reconcile.check(p, trail)
        return {"consistent": report["consistent"], "run_active": report.get("run_active", False),
                "json_trail": report["json"], "audit_db": report["audit_db"], "incomplete": report["incomplete"],
                "cross_reference_problems": report["cross_reference_problems"],
                "note": "The JSON trail and the SQLite databases cannot be committed atomically together. Gaps are detected "
                        "here and reconciled from audit.db and the working copy's reset stamp, never assumed."}

    return app


app = create_app()
