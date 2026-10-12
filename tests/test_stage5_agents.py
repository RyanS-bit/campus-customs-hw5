"""Stage 5: the five-agent team, delegation, permissions, audit integration and model configuration.

All runs are mode='simulated' with scripted OFFLINE models (tests/agent_script.py), on temporary
databases and temporary audit trails. Nothing here contacts Portkey or any model.
"""

import asyncio
import itertools
import json
import re
import sqlite3
import uuid
from contextlib import closing

import pytest

import agents
import audit_reconcile
import audit_trail
import config
import locks
import maintenance
import models
import runner
import server
from agent_script import AGENTS, Scripted
from conftest import REAL_ROOT, counts, privileged

PAY = {"kind": "invoice", "ref_id": 501, "amount": 840, "ticket_id": 101, "rationale": "Pay overdue invoice 501"}
QUOTE = {"ticket_id": 103, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 20, "unit_price": 45, "rationale": "Bulk discount within policy"}


def run(work, script, entry="boss", budget=None, task="Handle the open tickets", **kw):
    return asyncio.run(runner.run_task(task, p=work, model=script.models(), mode="simulated", entry=entry,
                                       budget=budget, launcher=runner.in_memory_launcher, **kw))


def events(work):
    return audit_trail.AuditTrail(work.audit_trail_json).read()


def of_type(evs, t):
    return [e for e in evs if e["event_type"] == t]


# ---- construction and configuration ---------------------------------------------------

def test_a1_all_five_agents_build_on_the_single_configured_gpt6_luna_model(work):
    cfg = config.load_config(env={"PORTKEY_API_KEY": "dummy-offline-key", "PORTKEY_BASE_URL": "http://127.0.0.1:9/v1"},
                             env_files=())
    model = config.build_model(cfg)
    toolsets = {a: runner.make_toolset(a, server.create_server(None, work)) for a in AGENTS}
    team = agents.build_team(model, toolsets)
    assert set(team) == set(AGENTS)
    assert all(a.model is model for a in team.values())                      # ONE shared model object
    assert model.model_name == "gpt-6-luna" and config.is_required_model(model)
    for name, agent in team.items():
        assert agent.name == name and agent.output_type is models.AgentReport


def test_a2_each_agent_sees_exactly_its_approved_tools_plus_delegate(work):
    s = Scripted({a: [[]] for a in AGENTS})
    for a in AGENTS:
        assert run(work, s, entry=a).status == "completed"
    for a in AGENTS:
        assert s.tools_seen[a] == [sorted(server.ACCESS[a] | {"delegate"})]
    assert not any(t in s.tools_seen["boss"][0] for t in server.WRITE_TOOLS)   # Boss is read-only


def test_a2_client_filter_alone_restricts_tools_even_if_a_server_exposed_all_ten(work):
    """Second layer: give every agent a development-mode server (all ten tools listed). The agent-side
    filter must still show each agent only its approved tools."""
    s = Scripted({a: [[]] for a in AGENTS})
    for a in AGENTS:
        out = asyncio.run(runner.run_task("x", p=work, model=s.models(), mode="simulated", entry=a,
                                          launcher=lambda p: (lambda agent, identity: server.create_server(None, p))))
        assert out.status == "completed"
    for a in AGENTS:
        assert s.tools_seen[a] == [sorted(server.ACCESS[a] | {"delegate"})]


def test_a3_prompt_files_exist_name_their_tools_and_share_the_rules():
    rules = None
    for a in AGENTS:
        text = agents.load_prompt(a)
        for tool in server.ACCESS[a]:
            assert f"`{tool}`" in text, (a, tool)
        for tool in set(server.WRITE_TOOLS) - server.ACCESS[a]:
            assert f"`{tool}`" not in text.split("## Rules")[0], (a, tool)
        shared = text[text.index("## Rules"):]
        rules = rules or shared
        assert shared == rules                                                 # identical common rules
    for phrase in ("Facts come only from your MCP tools", "You prepare; humans decide", "Cash never goes negative",
                   "Unpaid invoices block a vendor", "No revenue is modelled", "Nothing is sent", "Stay within your authority"):
        assert phrase in rules


@pytest.mark.parametrize("value,ok", [("gpt-6-luna", True), ("@course-openai/gpt-6-luna", True), ("gpt-4o", False),
                                      ("gpt-6-luna-mini", False), ("claude-opus-5-5", False), ("gpt-5.6-luna", False)])
def test_a4_only_gpt6_luna_is_accepted(value, ok):
    env = {"PORTKEY_MODEL": value, "PORTKEY_API_KEY": "dummy-offline-key"}
    if ok:
        assert config.load_config(env=env, env_files=()).model_name == value
    else:
        with pytest.raises(config.ConfigError):
            config.load_config(env=env, env_files=())


def test_a4_configuration_sources_and_secrecy(tmp_path):
    root_env, local_env = tmp_path / "root.env", tmp_path / "local.env"
    root_env.write_text("PORTKEY_API_KEY=key-from-root-file-123\n")
    local_env.write_text("PORTKEY_BASE_URL=http://example.invalid/v1\n")
    cfg = config.load_config(env={}, env_files=(local_env, root_env))
    assert (cfg.model_name, cfg.base_url, cfg.api_key) == ("gpt-6-luna", "http://example.invalid/v1", "key-from-root-file-123")
    assert "key-from-root-file-123" not in repr(cfg) and "<set>" in repr(cfg)
    assert config.load_config(env={"PORTKEY_API_KEY": "from-env-456"}, env_files=(root_env,)).api_key == "from-env-456"
    assert config.ENV_FILES == (REAL_ROOT / ".env", REAL_ROOT.parent / ".env")      # hw5, then the course root (HW3/HW4)
    with pytest.raises(config.ConfigError, match="PORTKEY_API_KEY"):
        config.build_model(config.load_config(env={}, env_files=()))


def test_a5_live_and_simulated_guards(work):
    s = Scripted()
    real = config.build_model(config.ModelConfig("gpt-6-luna", "http://127.0.0.1:9/v1", "dummy-offline-key"))
    with pytest.raises(ValueError, match="Live runs must use"):
        asyncio.run(runner.run_task("x", p=work, model=s.models(), mode="live", live_authorization="test"))
    with pytest.raises(PermissionError, match="authorization"):
        asyncio.run(runner.run_task("x", p=work, model=real, mode="live"))
    with pytest.raises(ValueError, match="offline test model"):
        asyncio.run(runner.run_task("x", p=work, model=real, mode="simulated"))
    with pytest.raises(ValueError):
        asyncio.run(runner.run_task("x", p=work, model=s.models(), mode="demo"))
    assert not work.audit_trail_json.exists()                                  # refused before anything ran


# ---- delegation ----------------------------------------------------------------------

@pytest.mark.parametrize("src,dst", list(itertools.permutations(AGENTS, 2)))
def test_a6_every_agent_can_delegate_to_every_other_agent(work, src, dst):
    s = Scripted({src: [[("delegate", dst, f"Task from {src}")]]})
    out = run(work, s, entry=src)
    assert out.status == "completed" and s.runs == [src, dst]
    evs = events(work)
    started = [e for e in of_type(evs, "agent_started") if e["actor"] == dst]
    assert started and started[0]["chain"] == [src, dst]
    done = of_type(evs, "delegation_completed")
    assert done and done[0]["actor"] == src and done[0]["details"]["to"] == dst and done[0]["outcome"]["status"] == "ok"
    reply = [c for n, c in s.returns[src] if n == "delegate"][0]
    assert json.loads(reply)["from"] == dst


def test_a7_cycles_are_refused_and_the_run_continues(work):
    s = Scripted({"boss": [[("delegate", "inventory", "Check hoodie stock")]],
                  "inventory": [[("delegate", "accounting", "Can we afford a reprint?")]],
                  "accounting": [[("delegate", "inventory", "Loop back"), ("delegate", "boss", "Loop to the top")]]})
    out = run(work, s)
    assert out.status == "completed" and s.runs == ["boss", "inventory", "accounting"]
    refused = of_type(events(work), "delegation_refused")
    assert [(e["actor"], e["details"]["to"], e["outcome"]["code"]) for e in refused] == [
        ("accounting", "inventory", "CYCLE"), ("accounting", "boss", "CYCLE")]
    assert all("already working on this chain" in c for n, c in s.returns["accounting"] if n == "delegate")


def test_a7_self_delegation_is_a_cycle(work):
    s = Scripted({"boss": [[("delegate", "boss", "Do it yourself")]]})
    assert run(work, s).status == "completed" and s.runs == ["boss"]
    assert of_type(events(work), "delegation_refused")[0]["outcome"]["code"] == "CYCLE"


def test_a8_depth_limit_and_the_full_five_agent_chain(work):
    chain = ["boss", "customer_service", "inventory", "accounting", "facilities"]
    plans = {a: [[("delegate", chain[i + 1], "next")]] for i, a in enumerate(chain[:-1])}
    assert run(work, Scripted(plans)).status == "completed"                    # 4 hops allowed by default
    assert [e["chain"] for e in of_type(events(work), "agent_started")][-1] == chain
    s = Scripted({a: [[("delegate", chain[i + 1], "next")]] for i, a in enumerate(chain[:-1])})
    run(work, s, budget=models.RunBudget(max_depth=2))
    refused = of_type(events(work), "delegation_refused")
    assert refused[-1]["outcome"]["code"] == "DEPTH" and refused[-1]["chain"] == chain[:3]
    assert s.runs == chain[:3]


def test_a9_sequential_consultations_of_the_same_agent_are_allowed(work):
    s = Scripted({"boss": [[("delegate", "inventory", "first"), ("delegate", "inventory", "second")]]})
    assert run(work, s).status == "completed" and s.runs == ["boss", "inventory", "inventory"]
    assert not of_type(events(work), "delegation_refused")


def test_a10_delegation_budget_stops_the_run(work):
    s = Scripted({"boss": [[("delegate", "inventory", "1"), ("delegate", "accounting", "2"), ("delegate", "facilities", "3")]]})
    out = run(work, s, budget=models.RunBudget(max_delegations=2))
    assert out.status == "stopped" and "delegation budget" in out.error
    evs = events(work)
    assert of_type(evs, "budget_exceeded")[0]["outcome"]["code"] == "DELEGATION_BUDGET"
    assert of_type(evs, "run_finished")[0]["outcome"]["run_status"] == "stopped"
    assert "facilities" not in s.runs


def test_a11_usage_is_shared_across_nested_runs_and_limited_once(work):
    plans = lambda: {"boss": [[("delegate", "inventory", "a")]], "inventory": [[("delegate", "accounting", "b")]]}
    ok = run(work, Scripted(plans()), budget=models.RunBudget(request_limit=20))
    assert ok.status == "completed" and ok.usage["requests"] == 5              # boss 2 + inventory 2 + accounting 1
    stopped = run(work, Scripted(plans()), budget=models.RunBudget(request_limit=4))
    assert stopped.status == "stopped" and "request_limit" in stopped.error
    assert of_type(events(work), "budget_exceeded")[-1]["outcome"]["code"] == "USAGE_LIMIT"


# ---- permissions cannot be bypassed --------------------------------------------------

@pytest.mark.parametrize("delegator,actor,tool,args", [
    ("accounting", "customer_service", "propose_payment", PAY),   # Accounting delegates, but CS still lacks the tool
    ("customer_service", "boss", "propose_quote", QUOTE),          # Boss stays read-only even when asked
    ("boss", "facilities", "propose_payment", PAY),
    ("boss", "inventory", "draft_message", {"ticket_id": 101, "subject": "x", "body": "A message from inventory."}),
])
def test_a12_tool_permissions_cannot_be_bypassed_through_delegation(work, delegator, actor, tool, args):
    s = Scripted({delegator: [[("delegate", actor, f"Please call {tool}")]], actor: [[("tool", tool, args)]]})
    if delegator == actor:
        pytest.skip("same agent")
    out = run(work, s, entry=delegator)
    assert out.status == "completed"
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}
    assert any(n == tool and "Unknown tool" in str(c) for n, c in s.returns[actor])
    assert not [e for e in of_type(events(work), "tool_call_started") if e["tool"] == tool]


def test_a12_drafting_outside_authority_is_refused_by_the_server_and_recorded(work):
    s = Scripted({"customer_service": [[("tool", "draft_message", {"ticket_id": 102, "subject": "Rent",
                                                                    "body": "Customer service writing to the landlord."})]]})
    run(work, s, entry="customer_service")
    finished = of_type(events(work), "tool_call_finished")[0]
    assert finished["outcome"]["status"] == "refused" and finished["outcome"]["code"] == "NOT_PERMITTED"
    returned = [c for n, c in s.returns["customer_service"] if n == "draft_message"][0]
    assert returned["ok"] is False and "delegate this communication to facilities" in returned["error"]
    assert counts(work)["drafts"] == 0


def test_a13_forged_identity_in_arguments_confers_nothing(work):
    s = Scripted({"customer_service": [[("tool", "propose_quote", {**QUOTE, "agent": "accounting", "run_id": str(uuid.uuid4())}),
                                        ("delegate", "ceo", "escalate")]]})
    out = run(work, s, entry="customer_service")
    assert out.status == "completed" and counts(work)["pending_actions"] == 0
    assert not [e for e in of_type(events(work), "agent_started") if e["actor"] not in AGENTS]
    assert {e["actor"] for e in events(work) if e["actor_kind"] == "agent"} == {"customer_service"}


# ---- audit integration ----------------------------------------------------------------

def test_a14_writes_by_agents_are_attributed_and_linked_end_to_end(work):
    s = Scripted({"boss": [[("delegate", "accounting", "Propose paying invoice 501"),
                            ("delegate", "customer_service", "Quote ticket 103")]],
                  "accounting": [[("tool", "propose_payment", PAY)]],
                  "customer_service": [[("tool", "propose_quote", QUOTE)]]})
    out = run(work, s)
    assert out.status == "completed"
    evs = events(work)
    run_id = out.run_id
    assert {e["run_id"] for e in evs} == {run_id} and {e["mode"] for e in evs} == {"simulated"}
    finished = {e["tool"]: e for e in of_type(evs, "tool_call_finished")}
    with privileged(work) as con:
        rows = {r[0]: r[1:] for r in con.execute("SELECT kind, proposed_by, run_id, call_id FROM pending_actions")}
        db_events = {r[0]: r[1:] for r in con.execute("SELECT call_id, actor, run_id FROM audit.events")}
    assert rows["payment"] == ("accounting", run_id, finished["propose_payment"]["call_id"])
    assert rows["quote"] == ("customer_service", run_id, finished["propose_quote"]["call_id"])
    assert db_events[finished["propose_payment"]["call_id"]] == ("accounting", run_id)
    assert finished["propose_payment"]["chain"] == ["boss", "accounting"]
    report = audit_reconcile.check(work)
    assert report["consistent"], report


def test_a15_run_lifecycle_and_usage_labels(work):
    out = run(work, Scripted())
    evs = events(work)
    assert [e["event_type"] for e in evs] == ["run_started", "agent_started", "agent_finished", "run_finished"]
    assert evs[0]["details"]["model"].startswith("offline test models")
    assert evs[-1]["usage"]["source"] == "offline test model: synthetic counts, NOT real model usage"
    assert evs[-1]["usage"]["requests"] == out.usage["requests"] == 1
    finished = of_type(evs, "agent_finished")[0]
    assert "links" not in finished or finished["links"] is None
    assert finished["details"]["note"].startswith("self-reported")


def test_a16_child_failure_is_reported_to_the_parent_and_root_failure_ends_the_run(work):
    s = Scripted({"boss": [[("delegate", "inventory", "check")]], "inventory": [[("raise", "simulated model failure")]]})
    out = run(work, s)
    assert out.status == "completed"
    done = of_type(events(work), "delegation_completed")[0]
    assert done["outcome"]["status"] == "error" and "simulated model failure" in done["outcome"]["message"]
    assert of_type(events(work), "agent_finished")[0]["outcome"]["status"] == "error"
    out2 = run(work, Scripted({"boss": [[("raise", "root model failure")]]}))
    assert out2.status == "error" and "root model failure" in out2.error
    assert of_type(events(work), "run_finished")[-1]["outcome"]["run_status"] == "error"


def test_a17_interrupted_audit_write_leaves_a_reconcilable_gap(work, monkeypatch):
    trail = audit_trail.AuditTrail(work.audit_trail_json)
    real = trail.append_with

    def failing(build):
        if any(e["event_type"] == "tool_call_finished" for e in build([])):
            raise audit_trail.AuditTrailError("simulated JSON failure after the database committed")
        return real(build)
    monkeypatch.setattr(trail, "append_with", failing)
    s = Scripted({"accounting": [[("tool", "propose_payment", PAY)]]})
    with pytest.raises(audit_trail.AuditTrailError):
        asyncio.run(runner.run_task("x", p=work, model=s.models(), mode="simulated", entry="accounting",
                                    launcher=runner.in_memory_launcher, trail=trail))
    monkeypatch.undo()
    assert counts(work)["pending_actions"] == 1                                # the database change committed
    assert not locks.run_active(work.active_dir)                              # run lock released
    gap = audit_reconcile.check(work)["incomplete"]
    assert len(gap) == 1 and gap[0]["db_outcome"] == "committed"
    added = audit_reconcile.reconcile(work)
    assert len(added) == 1 and audit_reconcile.reconcile(work) == []


def test_a18_run_lock_is_held_for_the_whole_run(work, monkeypatch):
    seen = []
    real_hook = runner.audit_hook

    def spying(agent):
        inner = real_hook(agent)
        async def hook(ctx, call_tool, name, args):
            seen.append(locks.run_active(work.active_dir))
            with pytest.raises(maintenance.ResetError, match="run is active"):
                maintenance.reset_working_db(work)
            return await inner(ctx, call_tool, name, args)
        return hook
    monkeypatch.setattr(runner, "audit_hook", spying)
    run(work, Scripted({"boss": [[("tool", "get_open_tickets", {})]]}))
    assert seen == [True] and not locks.run_active(work.active_dir)


def test_a19_a_second_concurrent_run_is_refused(work):
    with audit_trail.RunRecorder(audit_trail.AuditTrail(work.audit_trail_json), str(uuid.uuid4()), "simulated",
                                 active_dir=work.active_dir):
        with pytest.raises(locks.LockTimeout):
            run(work, Scripted())


# ---- stdio: identity really comes from each server's launch environment -----------------

def test_a20_stdio_launch_one_server_per_agent(tmp_path):
    import paths
    p = paths.resolve(env={"CC_RUNTIME_DIR": str(tmp_path / "rt")}, root=REAL_ROOT, use_dotenv=False)
    maintenance.reset_working_db(p)
    trail = audit_trail.AuditTrail(tmp_path / "audit_trail.json")
    s = Scripted({"boss": [[("delegate", "accounting", "Propose paying invoice 501")]],
                  "accounting": [[("tool", "propose_payment", PAY)]]})
    out = asyncio.run(runner.run_task("x", p=p, model=s.models(), mode="simulated", trail=trail))   # default: stdio launcher
    assert out.status == "completed"
    with closing(sqlite3.connect(p.working_db)) as con:
        assert con.execute("SELECT proposed_by, run_id FROM pending_actions").fetchall() == [("accounting", out.run_id)]
    assert s.tools_seen["accounting"] == [sorted(server.ACCESS["accounting"] | {"delegate"})]


# ---- static checks --------------------------------------------------------------------

def test_a21_agent_code_never_opens_sqlite_and_has_no_approval_path():
    for f in (REAL_ROOT / "backend").glob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert not re.search(r"^\s*import sqlite3|sqlite3\.connect", src, re.MULTILINE), f.name   # main.py included
        if f.name == "main.py":
            continue    # Problem 7: the human HTTP routes (approve/reject) live here; agents cannot reach HTTP
        assert not re.search(r"def\s+(approve|reject|execute|send|pay)\w*\s*\(", src), f.name


def test_a22_single_request_settings_reach_the_client():
    cfg = config.ModelConfig("gpt-6-luna", "http://127.0.0.1:9/v1", "dummy-offline-key")
    single = config.build_model(cfg, timeout=30, max_retries=0)
    assert single.client.max_retries == 0 and single.client.timeout == 30
    default = config.build_model(cfg)
    assert default.client.max_retries == 2                                     # unchanged default for agent runs
