"""Problem 9, follow-up 20: every agent's FULL reply is kept in output/run_responses/<run_id>.jsonl.

All tests are offline: scripted FunctionModels, the in-memory MCP launcher and temporary copies of the
hw5 layout (conftest `work`). No network, no model API, no Portkey. The real audit trail and the real
Attempt 1-3 evidence are only READ (test r12); the conftest session guard fails the run if they change.
"""
import asyncio
import json
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

import audit_trail
import main
import run_responses
import runner
from agent_script import Scripted
from conftest import REAL_ROOT

LONG = {a: f"{a} full reply: " + " ".join(f"[{a} detail {i}]" for i in range(250)) for a in
        ("boss", "inventory", "accounting", "facilities", "customer_service")}      # each about 5,000 characters
ATTEMPT_RUNS = {"attempt 1": "53e4f001", "attempt 2": "ae8d9bf4-f61f-4117-b7f4-f08b8626e139",
                "attempt 3": "84270c9b-53f9-455e-b956-93b3fd77f698"}


def run(work, script, entry="boss", **kw):
    return asyncio.run(runner.run_task("Handle ticket 101", p=work, model=script.models(), mode="simulated",
                                       entry=entry, launcher=runner.in_memory_launcher, **kw))


def trail(work):
    return audit_trail.AuditTrail(work.audit_trail_json).read()


def path_for(work, run_id):
    return run_responses.responses_path(work.audit_trail_json, run_id)


def four_specialists():
    return Scripted({
        "boss": [[("delegate", "accounting", "Review invoice 501 and cash"),
                  ("delegate", "inventory", "Check size S stock"),
                  ("delegate", "facilities", "Check the lease"),
                  ("delegate", "customer_service", "Draft an update"),
                  ("final", {"summary": LONG["boss"], "needs_human": ["Decide on 501"]})]],
        "accounting": [[("tool", "get_financial_position", {}),
                        ("final", {"summary": LONG["accounting"], "estimates": ["$160 after rent (arithmetic)"]})]],
        "inventory": [[("tool", "check_inventory", {"sku": "CC-TEE-WHITE"}), ("final", {"summary": LONG["inventory"]})]],
        "facilities": [[("final", {"summary": LONG["facilities"], "unknowns": ["renewal terms"]})]],
        "customer_service": [[("final", {"summary": LONG["customer_service"]})]],
    })


# ---------------------------------------------------------------- preservation

def test_r1_every_agent_reply_is_stored_in_full_with_identity_and_order(work):
    out = run(work, four_specialists())
    assert out.status == "completed"
    recs = run_responses.read(path_for(work, out.run_id))
    assert [r["record_seq"] for r in recs] == list(range(1, 7))
    assert recs[0]["kind"] == "responses_opened" and recs[0]["run_id"] == out.run_id
    replies = recs[1:]
    # chronological: each specialist finishes before the boss, in the order it was consulted
    assert [r["agent"] for r in replies] == ["accounting", "inventory", "facilities", "customer_service", "boss"]
    for r in replies:
        assert r["kind"] == "agent_response" and r["run_id"] == out.run_id
        assert r["response"]["summary"] == LONG[r["agent"]]                       # complete, untruncated
        assert len(r["response"]["summary"]) > 4000
    specialists = replies[:4]
    assert all(r["role"] == "specialist" and r["chain"] == ["boss", r["agent"]] and r["delegated_by"] == "boss"
               for r in specialists)
    assert replies[0]["task"] == "Review invoice 501 and cash"
    assert replies[0]["response"]["estimates"] == ["$160 after rent (arithmetic)"]
    assert replies[2]["response"]["unknowns"] == ["renewal terms"]
    boss = replies[4]
    assert boss["role"] == "entry" and boss["delegation_id"] is None and boss["task"] == "Handle ticket 101"
    assert len({r["delegation_id"] for r in specialists}) == 4                   # one distinct ID per delegation


def test_r2_trail_keeps_its_summaries_and_event_types_and_points_to_the_records(work):
    out = run(work, four_specialists())
    evs = [e for e in trail(work) if e["run_id"] == out.run_id]
    assert audit_trail.verify_events(trail(work))["valid"]
    assert {e["event_type"] for e in evs} <= set(audit_trail.EVENT_TYPES)       # no new event types
    finished = [e for e in evs if e["event_type"] == "agent_finished"]
    recs = {r["record_seq"]: r for r in run_responses.read(path_for(work, out.run_id))}
    for e in finished:
        assert len(e["result_summary"]) == 300 and e["result_summary"].endswith("...")   # summary unchanged
        ref = e["details"]["response_record"]
        r = recs[ref["record_seq"]]
        assert ref["record_hash"] == r["hash"] and ref["file"] == f"run_responses/{out.run_id}.jsonl"
        assert r["trail_event_id"] == e["event_id"] and r["response_sha256"] == e["result_sha256"]
        assert e["details"]["delegation_id"] == r["delegation_id"]
    started = next(e for e in evs if e["event_type"] == "run_started")
    assert started["details"]["responses_file"] == f"run_responses/{out.run_id}.jsonl"


def test_r3_delegation_ids_link_request_child_and_completion(work):
    out = run(work, four_specialists())
    evs = [e for e in trail(work) if e["run_id"] == out.run_id]
    for kind in ("delegation_requested", "delegation_completed"):
        ids = [e["details"]["delegation_id"] for e in evs if e["event_type"] == kind]
        assert len(ids) == 4 and all(uuid.UUID(i).version == 4 for i in ids)
    req = {e["details"]["delegation_id"]: e["details"]["to"] for e in evs if e["event_type"] == "delegation_requested"}
    for e in evs:
        if e["event_type"] in ("agent_started", "agent_finished") and e["actor"] != "boss":
            assert req[e["details"]["delegation_id"]] == e["actor"]


def test_r4_reconciliation_is_consistent_for_a_clean_run(work):
    out = run(work, four_specialists())
    check = run_responses.reconcile(trail(work), out.run_id, path_for(work, out.run_id))
    assert check["consistent"] and check["recorded"] and check["problems"] == []
    assert (check["records"], check["responses"], check["unavailable"]) == (6, 5, 0)


# ---------------------------------------------------------------- integrity

def _rewrite(path, mutate):
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(mutate(lines)) + "\n", encoding="utf-8")


def test_r5_edited_reply_is_detected(work):
    out = run(work, four_specialists())
    p = path_for(work, out.run_id)

    def edit(lines):
        r = json.loads(lines[1]); r["response"]["summary"] = "a shorter, altered reply"
        lines[1] = json.dumps(r); return lines
    _rewrite(p, edit)
    problems = run_responses.reconcile(trail(work), out.run_id, p)["problems"]
    assert any("hash chain broken" in x for x in problems)
    assert any("does not match its response_sha256" in x for x in problems)


def test_r6_removed_record_and_removed_file_are_detected(work):
    out = run(work, four_specialists())
    p = path_for(work, out.run_id)
    _rewrite(p, lambda lines: lines[:2] + lines[3:])
    check = run_responses.reconcile(trail(work), out.run_id, p)
    assert not check["consistent"] and any("references a response record that is missing" in x for x in check["problems"])
    p.unlink()
    check = run_responses.reconcile(trail(work), out.run_id, p)
    assert not check["consistent"] and check["problems"] == ["response file missing although the trail references it"]


def test_r7_existing_file_is_never_overwritten_and_the_run_stops_cleanly(work):
    run_id = str(uuid.uuid4())
    p = path_for(work, run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("earlier evidence\n", encoding="utf-8")
    s = four_specialists()
    out = run(work, s, run_id=run_id)
    assert out.status == "error" and "refusing to overwrite" in out.error
    assert p.read_text(encoding="utf-8") == "earlier evidence\n"                  # untouched
    assert s.runs == []                                                         # no agent ran
    evs = [e for e in trail(work) if e["run_id"] == run_id]
    assert [e["event_type"] for e in evs] == ["run_started", "error", "run_finished"]
    assert evs[1]["outcome"]["code"] == "RESPONSE_LOG"


def test_r8_failed_specialist_is_recorded_as_unavailable_not_invented(work):
    s = Scripted({"boss": [[("delegate", "inventory", "check"), ("final", {"summary": "inventory failed"})]],
                  "inventory": [[("raise", "simulated model failure")]]})
    out = run(work, s)
    assert out.status == "completed"
    recs = run_responses.read(path_for(work, out.run_id))
    gap = recs[1]
    assert gap["kind"] == "agent_response_unavailable" and gap["agent"] == "inventory"
    assert gap["response"] is None and gap["response_sha256"] is None and gap["error_code"] == "RuntimeError"
    assert run_responses.reconcile(trail(work), out.run_id, path_for(work, out.run_id))["consistent"]


def test_r9_refused_delegations_carry_an_id_and_create_no_reply(work):
    s = Scripted({"boss": [[("delegate", "boss", "loop"), ("final", {"summary": "done"})]]})
    out = run(work, s)
    refused = [e for e in trail(work) if e["run_id"] == out.run_id and e["event_type"] == "delegation_refused"]
    assert refused and uuid.UUID(refused[0]["details"]["delegation_id"])
    assert [r["agent"] for r in run_responses.read(path_for(work, out.run_id))[1:]] == ["boss"]


def test_r10_concurrent_appends_keep_one_ordered_chain(tmp_path):
    log = run_responses.ResponseLog(tmp_path / "r.jsonl", str(uuid.uuid4()), "simulated")
    log.open(run_started_event_id=None, ticket_id=None)

    def write(i):
        log.agent_response(agent="inventory", chain=["boss", "inventory"], delegation_id=str(i), task="t",
                           response={"summary": str(i)}, trail_event_id=str(i))
    threads = [threading.Thread(target=write, args=(i,)) for i in range(40)]
    [t.start() for t in threads]; [t.join() for t in threads]
    recs = run_responses.read(tmp_path / "r.jsonl")
    assert [r["record_seq"] for r in recs] == list(range(1, 42))
    assert all(recs[i]["prev_hash"] == recs[i - 1]["hash"] for i in range(1, len(recs)))


def test_r11_a_reply_containing_a_credential_is_withheld_but_still_matchable(tmp_path):
    secret = "sk-test-" + uuid.uuid4().hex
    audit_trail.register_secret(secret)
    try:
        log = run_responses.ResponseLog(tmp_path / "r.jsonl", str(uuid.uuid4()), "simulated")
        log.open(run_started_event_id=None, ticket_id=None)
        reply = {"summary": f"key is {secret}"}
        r = log.agent_response(agent="boss", chain=["boss"], delegation_id=None, task="t", response=reply, trail_event_id="e")
        assert r["kind"] == "agent_response_withheld" and r["response"] is None
        assert secret not in (tmp_path / "r.jsonl").read_text(encoding="utf-8")
        assert r["response_sha256"] == audit_trail.sha256_text(audit_trail.canonical(reply))
    finally:
        audit_trail._REGISTERED_SECRETS.discard(secret)


# ---------------------------------------------------------------- earlier runs: preserved, never reconstructed

def test_r12_attempts_1_to_3_have_no_response_file_and_nothing_is_reconstructed():
    real_trail = REAL_ROOT / "output" / "audit_trail.json"
    events = audit_trail.AuditTrail(real_trail).read()                         # read-only
    ids = {e["run_id"] for e in events if e.get("event_type") == "run_started"}
    for label, prefix in ATTEMPT_RUNS.items():
        run_id = next(i for i in ids if i.startswith(prefix))
        check = run_responses.reconcile(events, run_id, run_responses.responses_path(real_trail, run_id))
        assert check["recorded"] is False and check["consistent"] is True and check["records"] == 0, label
        assert "not complete transcripts" in check["note"] and "not been reconstructed" in check["note"]


# ---------------------------------------------------------------- backend route and dashboard compatibility

def _client(work, script):
    async def start(ticket_id, run_id):
        return await runner.run_task(main.ticket_task(ticket_id), p=work, model=script.models(), mode="simulated",
                                     launcher=runner.in_memory_launcher, run_id=run_id, ticket_id=ticket_id)
    return TestClient(main.create_app(p=work, run_factory=start, run_mode="simulated"))


def _finish(c, run_id):
    import time
    for _ in range(1200):
        view = c.get(f"/api/runs/{run_id}").json()
        if view["phase"] not in ("queued", "active"):
            return view
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_r13_responses_route_serves_full_replies_and_the_run_view_is_unchanged(work):
    with _client(work, four_specialists()) as c:
        run_id = c.post("/api/tickets/101/run", headers={"X-Human-Decision": "confirm"}, ).json()["run_id"]
        view = _finish(c, run_id)
        assert view["phase"] == "completed"
        assert set(view) == set(main.RunView.model_fields)                         # dashboard's run view: same shape
        body = c.get(f"/api/runs/{run_id}/responses").json()
        assert body["recorded"] and body["consistent"] and body["reconciliation"]["problems"] == []
        assert [r["agent"] for r in body["records"][1:]] == ["accounting", "inventory", "facilities", "customer_service", "boss"]
        assert body["records"][1]["response"]["summary"] == LONG["accounting"]
        ev = c.get(f"/api/events?run_id={run_id}").json()["events"]
        assert all(set(e) == set(main.EventView.model_fields) for e in ev)        # events feed: same shape
        assert c.post(f"/api/runs/{run_id}/responses", headers={"X-Human-Decision": "confirm"}).status_code == 405


@pytest.mark.parametrize("bad", ["not-a-uuid", "..%2F..%2Fdata%2Fcampus_customs", str(uuid.uuid4()),
                                 str(uuid.uuid4()).upper()])
def test_r14_unknown_or_non_canonical_run_ids_are_404(work, bad):
    with TestClient(main.create_app(p=work)) as c:
        r = c.get(f"/api/runs/{bad}/responses")
        assert r.status_code == 404                     # the traversal form never even matches the route
        if "%2F" not in bad:
            assert r.json()["error"]["code"] == "NOT_FOUND"


def test_r15_a_run_without_a_file_is_reported_as_not_recorded(work):
    out = run(work, four_specialists())
    path_for(work, out.run_id).rename(path_for(work, out.run_id).with_suffix(".moved"))
    # simulate an earlier-format run: same trail run, but its agent_finished events never referenced a file
    legacy = str(uuid.uuid4())
    rec = audit_trail.RunRecorder(audit_trail.AuditTrail(work.audit_trail_json), legacy, "simulated")
    rec.run_started(model="scripted")
    rec.record("agent_finished", "boss", chain=["boss"], outcome={"status": "ok"}, result_summary="short summary")
    rec.run_finished(status="completed")
    with TestClient(main.create_app(p=work)) as c:
        body = c.get(f"/api/runs/{legacy}/responses").json()
        assert body["recorded"] is False and body["consistent"] is True and body["records"] == []
        assert "not been reconstructed" in body["note"]
        moved = c.get(f"/api/runs/{out.run_id}/responses").json()
        assert moved["recorded"] is False and moved["consistent"] is False        # referenced but missing: flagged
