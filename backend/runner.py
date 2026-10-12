"""Run the agent team on a task (Problem 5, Stage 5).

  * One MCP server process PER AGENT, launched with CC_RUN_ID / CC_AGENT / CC_RUN_MODE: identity and
    permissions come from the launch, never from the model (spec section 7.1).
  * Every MCP tool call passes through an audit hook bound to the agent that owns the toolset:
    tool_call_started is saved BEFORE the call (if that fails, the call is not made), then
    tool_call_finished with outcome and links; the call_id travels in MCP _meta for correlation.
  * The RunRecorder holds the run lock for the whole run (reset/snapshot refuse meanwhile) and
    records run, agent, delegation, error and usage events in output/audit_trail.json.
  * mode='live' requires the configured gpt-6-luna model AND a recorded authorization;
    mode='simulated' refuses any real model. No live run happens unless both are deliberately supplied.
  * Follow-up 20: every run also keeps output/run_responses/<run_id>.jsonl with each agent's FULL reply
    (run_responses.py); the trail keeps its summaries unchanged. If that file cannot be written the run stops.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import uuid
from contextlib import AsyncExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

HW5 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HW5 / "mcp_server"))
sys.path.insert(0, str(HW5 / "backend"))

from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import StdioTransport  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402
from pydantic_ai.exceptions import UsageLimitExceeded  # noqa: E402
from pydantic_ai.mcp import MCPToolset  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402
from pydantic_ai.models.openai import OpenAIChatModel  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

import audit_trail  # noqa: E402
import config  # noqa: E402
import ops  # noqa: E402
import paths as paths_mod  # noqa: E402
import run_responses  # noqa: E402
from agents import build_team  # noqa: E402
from delegation import run_agent  # noqa: E402
from models import AGENT_NAMES, AgentReport, BudgetExceeded, RunBudget, RunDeps  # noqa: E402

SERVER_PY = HW5 / "mcp_server" / "server.py"
REFUSAL_NOTE = ("The tool refused or failed; nothing was created. Report this. If the request is outside "
                "your authority, delegate it to the responsible agent.")


def stdio_launcher(p: paths_mod.Paths):
    """Production: start mcp_server/server.py once per agent with that agent's identity in its environment."""
    def launch(agent: str, identity: ops.RunIdentity):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("CC_") and not audit_trail.CREDENTIAL_NAME.search(k)}
        env.update(CC_RUN_ID=identity.run_id, CC_AGENT=identity.agent, CC_RUN_MODE=identity.mode,
                   # Remediation M1: the database is the one THIS runner selected, passed explicitly.
                   CC_HW5_ROOT=str(p.root), CC_RUNTIME_DIR=str(p.active_dir) if p.mode == "runtime" else "",
                   CC_WORKING_DB=str(p.working_db))
        return Client(StdioTransport(command=sys.executable, args=[str(SERVER_PY)], env=env))
    return launch


def in_memory_launcher(p: paths_mod.Paths):
    """Offline tests: the same server code, built in-process for the same trusted identity."""
    import server
    return lambda agent, identity: server.create_server(identity, p)


def audit_hook(agent: str):
    """Bound to ONE agent's toolset; attribution comes from this binding, not from the model."""
    async def hook(ctx, call_tool, name, args):
        deps: RunDeps = ctx.deps
        if deps.current != agent:
            raise RuntimeError(f"toolset of {agent} used while {deps.current} is acting")

        async def invoke(call_id):
            try:
                return False, await call_tool(name, args, metadata={"call_id": call_id})
            except ToolError as exc:
                return True, str(exc)

        is_error, payload = await deps.recorder.tool_call(agent=agent, chain=list(deps.chain), tool=name, args=args,
                                                          invoke=invoke)
        if is_error:
            return {"ok": False, "error": payload, "note": REFUSAL_NOTE}
        return payload
    return hook


def make_toolset(agent: str, client) -> MCPToolset:
    return MCPToolset(client, tool_error_behavior="error", process_tool_call=audit_hook(agent), max_retries=2)


@dataclass
class RunOutcome:
    run_id: str
    mode: str
    status: str                      # completed | stopped | error
    report: AgentReport | None
    usage: dict
    error: str | None = None


def model_label(model) -> str:
    if isinstance(model, OpenAIChatModel):
        return model.model_name
    if isinstance(model, dict):
        return "offline test models: " + ", ".join(sorted({getattr(m, "model_name", type(m).__name__) for m in model.values()}))
    return f"offline test model: {getattr(model, 'model_name', type(model).__name__)}"


def usage_record(usage: RunUsage, mode: str) -> dict:
    return {"requests": usage.requests, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "tool_calls": usage.tool_calls,
            "source": ("provider-reported, via PydanticAI" if mode == "live"
                       else "offline test model: synthetic counts, NOT real model usage")}


OFFLINE_MODEL_TYPES = (FunctionModel, TestModel)


class OfflineViolation(RuntimeError):
    """A network connection was attempted during a simulated run."""


def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    return host in (None, "localhost", "::1", "") or (isinstance(host, str) and host.startswith("127."))


@contextmanager
def offline_network_guard():
    """Simulated runs only (remediation M2): any name lookup or socket connection to a non-local host
    raises OfflineViolation, so even a misbehaving test model cannot reach a real model service.
    Covers socket.getaddrinfo (all host names, including api.portkey.ai) and Python-level connects."""
    real_getaddrinfo, real_connect = socket.getaddrinfo, socket.socket.connect

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            raise OfflineViolation(f"network lookup of {host!r} blocked: simulated runs are offline")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) else None
        if not _is_local(host) and sock.family in (socket.AF_INET, socket.AF_INET6):
            raise OfflineViolation(f"connection to {address!r} blocked: simulated runs are offline")
        return real_connect(sock, address)

    socket.getaddrinfo, socket.socket.connect = guarded_getaddrinfo, guarded_connect
    try:
        yield
    finally:
        socket.getaddrinfo, socket.socket.connect = real_getaddrinfo, real_connect


def _client_limits(model) -> tuple[float | None, int | None]:
    client = getattr(model, "client", None)
    timeout = getattr(client, "timeout", None)
    if timeout is not None and not isinstance(timeout, (int, float)):
        parts = [getattr(timeout, k, None) for k in ("connect", "read", "write", "pool")]
        timeout = max((t for t in parts if t is not None), default=None)
    return timeout, getattr(client, "max_retries", None)


def _check_mode(model, mode: str, live_authorization: str | None, budget: RunBudget) -> None:
    models = list(model.values()) if isinstance(model, dict) else [model]
    if mode == "live":
        if not (len(models) == 1 and config.is_required_model(models[0])):
            raise ValueError("Live runs must use the single configured gpt-6-luna model for all five agents.")
        if not config.has_required_settings(models[0]):
            raise ValueError(f"Live model lacks the required settings {config.MODEL_SETTINGS} (the provider rejects tool "
                             "calls with reasoning on); build it with config.build_live_model(cfg, budget).")
        if not live_authorization:
            raise PermissionError("Live runs require a recorded human authorization (live_authorization=...).")
        timeout, retries = _client_limits(models[0])
        if timeout is None or timeout > budget.request_timeout_seconds or retries is None or retries > budget.max_http_retries:
            raise ValueError(f"Live model client exceeds the run budget (timeout {timeout}, retries {retries}); "
                             f"build it with config.build_live_model(cfg, budget).")
    elif mode == "simulated":
        # Remediation M2: an ALLOW-list. Only PydanticAI's offline test models; never a model name string.
        if not all(isinstance(m, OFFLINE_MODEL_TYPES) for m in models):
            raise ValueError("Simulated runs accept only offline test models (FunctionModel/TestModel).")
    else:
        raise ValueError(f"mode must be 'simulated' or 'live', not {mode!r}")


async def run_task(task: str, *, p: paths_mod.Paths, model, mode: str, entry: str = "boss",
                   budget: RunBudget | None = None, launcher=None, trail: audit_trail.AuditTrail | None = None,
                   live_authorization: str | None = None, run_id: str | None = None,
                   ticket_id: int | None = None) -> RunOutcome:
    """run_id / ticket_id (Problem 7): the backend chooses the run ID up front so it can return it at once,
    and records which ticket the run is for. Both are set by trusted code, never by a model."""
    budget = budget or RunBudget()
    _check_mode(model, mode, live_authorization, budget)
    if entry not in AGENT_NAMES:
        raise ValueError(f"unknown entry agent {entry!r}")
    if run_id is None:
        run_id = str(uuid.uuid4())
    elif ops.RunIdentity(run_id, entry, mode).run_id != run_id:     # validates a canonical UUID4
        raise ValueError("run_id must be a canonical UUID4")
    trail = trail or audit_trail.AuditTrail(p.audit_trail_json)
    launch = (launcher or stdio_launcher)(p)
    usage = RunUsage()
    status, report, error = "error", None, None
    guard = offline_network_guard() if mode == "simulated" else nullcontext()

    with audit_trail.RunRecorder(trail, run_id, mode, active_dir=p.active_dir) as rec, guard:
        started = rec.run_started(model=model_label(model), details={
            "entry_agent": entry, "ticket_id": ticket_id, "task": audit_trail.summarize(task), "budget": budget.describe(),
            "database_location": p.mode, "working_db": str(p.working_db), "live_authorization": live_authorization,
            "offline_network_guard": mode == "simulated",
            "responses_file": f"{run_responses.DIR_NAME}/{run_id}.jsonl"})
        try:
            responses = run_responses.ResponseLog(run_responses.responses_path(trail.path, run_id), run_id, mode)
            responses.open(run_started_event_id=started["event_id"], ticket_id=ticket_id)
            # Remediation m3: building the toolsets and team is inside the try, so run_finished always follows.
            toolsets = {a: make_toolset(a, launch(a, ops.RunIdentity(run_id, a, mode))) for a in AGENT_NAMES}
            deps = RunDeps(run_id=run_id, mode=mode, chain=(entry,), budget=budget, recorder=rec,
                           team=build_team(model, toolsets), responses=responses)
            async with AsyncExitStack() as stack:
                for ts in toolsets.values():
                    await stack.enter_async_context(ts)
                result = await asyncio.wait_for(run_agent(entry, task, deps, usage=usage),
                                                timeout=budget.wall_clock_seconds)
            report, status = result.output, "completed"
        except run_responses.ResponseLogError as exc:  # the trail still works: record the stop, then finish
            status, error = "error", f"ResponseLogError: {exc}"
            rec.record("error", "system", "system", outcome={"status": "error", "code": "RESPONSE_LOG",
                                                             "message": audit_trail.summarize(str(exc))})
        except audit_trail.AuditTrailError:
            raise                                    # cannot record anything further; the gap is reconcilable
        except TimeoutError:
            status, error = "stopped", f"wall-clock limit of {budget.wall_clock_seconds} s reached"
            rec.record("budget_exceeded", "system", "system", outcome={"status": "error", "code": "WALL_CLOCK",
                                                                       "message": error})
        except UsageLimitExceeded as exc:
            status, error = "stopped", str(exc)
            rec.record("budget_exceeded", "system", "system", outcome={"status": "error", "code": "USAGE_LIMIT",
                                                                       "message": audit_trail.summarize(str(exc))})
        except BudgetExceeded as exc:                # already recorded where it happened (delegate)
            status, error = "stopped", str(exc)
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"
            rec.record("error", "system", "system", outcome={"status": "error", "code": type(exc).__name__,
                                                             "message": audit_trail.summarize(str(exc))})
        rec.run_finished(status=status, usage=usage_record(usage, mode),
                         details={"error": audit_trail.summarize(error)} if error else None)
    return RunOutcome(run_id=run_id, mode=mode, status=status, report=report, usage=usage_record(usage, mode), error=error)
