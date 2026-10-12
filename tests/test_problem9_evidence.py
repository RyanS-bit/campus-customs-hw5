"""Problem 9, follow-up 47: validate the generated deliverables against the authentic records. READ-ONLY.

These tests read the REAL output files, databases (mode=ro) and audit trail; they write nothing. Reference values
that must not drift are fixed here, recorded BEFORE generation:
  * the SHA-256 of each Expected block in output/desk_tickets.html (computed 2026-10-11 from the Problem 6 file)
  * the SHA-256 of each final-state screenshot (captured 2026-10-11, follow-up 44)
"""
import base64
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from conftest import REAL_ROOT

sys.path.insert(0, str(REAL_ROOT / "scripts"))
import build_p9_evidence as gen  # noqa: E402

OUT = REAL_ROOT / "output"
DESK, JSONF, BOARD = OUT / "desk_tickets.html", OUT / "resolved_tickets.json", OUT / "resolved_board.html"
EXPECTED_BLOCK_SHA256 = {101: "a457da78134da640016b71ab39e3bcbb2833dc12d1c3248bf88545f135523a1d",
                         102: "4211a7d3ab9a553f8e10be38361fc61070d67f59409224feb08afc37c6619814",
                         103: "6b88c2e9a0fa235ef789fbddd9e9f8cc4a75174e16dae43c11726fac58a4d9f2"}
SCREENSHOT_SHA256 = {101: "da05ce2822f03769184100ef8122a998b275e35373a4a750402ef6d11a2f71b7",
                     102: "a99cee9da342b6d8dc0bfa6c51c77d57c805b86e28ba6ab75957ba755633eff0",
                     103: "0a6baa2500fdc6826652e0987b43d0c9953a5aa93fd65eddadea52a56010d3ce"}

pytestmark = pytest.mark.skipif(not JSONF.exists(), reason="Problem 9 deliverables not generated yet")


def ro(name):
    con = sqlite3.connect((REAL_ROOT / "data" / name).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


@pytest.fixture(scope="module")
def desk():
    return DESK.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def data():
    return json.loads(JSONF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def board():
    return BOARD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def db():
    w, a = ro("campus_customs_new.db"), ro("audit.db")
    out = {"tickets": {r["id"]: r["status"] for r in w.execute("SELECT id, status FROM tickets")},
           "payments": [dict(r) for r in w.execute("SELECT * FROM payments ORDER BY id")],
           "actions": [dict(r) for r in w.execute("SELECT * FROM pending_actions ORDER BY id")],
           "drafts": [dict(r) for r in w.execute("SELECT * FROM drafts ORDER BY id")],
           "cash": w.execute("SELECT balance FROM cash_accounts WHERE name = 'checking'").fetchone()[0],
           "resolved": {r["ref_id"]: json.loads(r["payload"]) for r in a.execute(
               "SELECT ref_id, payload FROM events WHERE event_type = 'ticket_resolved'")}}
    w.close(); a.close()
    return out


@pytest.fixture(scope="module")
def trail():
    import audit_trail, paths
    return audit_trail.AuditTrail(paths.resolve(env={}, root=REAL_ROOT, use_dotenv=False).audit_trail_json).read()


def section(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


# ---------------------------------------------------------------- Expected preserved

@pytest.mark.parametrize("t", [101, 102, 103])
def test_e1_expected_blocks_are_byte_for_byte_unchanged(desk, t):
    a = desk.index(f'id="panel-{t}"')
    block = desk[desk.index('<div class="card expected-block">', a):desk.index('<div class="card actual-block"', a)]
    assert hashlib.sha256(block.encode("utf-8")).hexdigest() == EXPECTED_BLOCK_SHA256[t]


def test_e2_placeholders_replaced_and_reflection_left_for_problem_10(desk):
    assert "actual-empty" not in re.sub(r"\.actual-empty \{[^}]*\}", "", desk)          # CSS rule kept, no placeholder left
    assert desk.count('data-section="actual"') == 3
    cash = section(desk, 'id="panel-cash"', "</section>")
    assert "coming-later" not in cash and 'data-section="cash-reconciliation"' in cash
    refl = section(desk, 'id="panel-reflection"', "</section>")
    assert "Coming later" not in refl and 'data-section="reflection"' in refl          # Problem 10 inserted


# ---------------------------------------------------------------- facts per ticket

@pytest.mark.parametrize("t", [101, 102, 103])
def test_f1_status_resolution_and_unverified_outcomes_match_the_records(data, db, desk, t):
    j = next(x for x in data["tickets"] if x["ticket_id"] == t)
    rec = db["resolved"][t]
    # Events written before follow-up 36 (ticket 102) carry no resolution_basis; they confirmed the business outcome.
    basis = rec.get("resolution_basis") or ("business_outcome" if rec["business_outcome_confirmed"] else None)
    assert j["final_status"] == db["tickets"][t] == "resolved"
    assert j["resolution"]["basis"] == basis
    assert j["resolution"]["business_outcome_confirmed"] is rec["business_outcome_confirmed"]
    assert j["resolution"]["unverified_outcomes"] == rec.get("unverified_outcomes", [])
    assert j["resolution"]["summary"] == rec["resolution_summary"]
    actual = section(desk, f'id="actual-{t}"', "</section>")
    assert basis in actual
    expected_basis = {101: "simulated_workflow_complete", 102: "business_outcome", 103: "simulated_workflow_complete"}[t]
    assert basis == expected_basis


@pytest.mark.parametrize("t", [101, 102, 103])
def test_f2_every_run_for_the_ticket_is_listed_with_its_outcome(data, trail, desk, t):
    runs = [e for e in trail if e["event_type"] == "run_started" and (e.get("details") or {}).get("ticket_id") == t]
    j = next(x for x in data["tickets"] if x["ticket_id"] == t)
    assert [r["run_id"] for r in j["runs"]] == [e["run_id"] for e in runs]
    status = {e["run_id"]: e["outcome"]["run_status"] for e in trail if e["event_type"] == "run_finished"}
    assert all(r["status"] == status[r["run_id"]] for r in j["runs"])
    actual = section(desk, f'id="actual-{t}"', "</section>")
    assert all(r["run_id"][:8] in actual for r in j["runs"])
    if t == 101:
        assert [r["status"] for r in j["runs"]] == ["error", "completed", "completed"]       # the failed attempt is shown


@pytest.mark.parametrize("t", [101, 102, 103])
def test_f3_delegations_tool_calls_and_contributions_match_the_trail(data, trail, t):
    j = next(x for x in data["tickets"] if x["ticket_id"] == t)
    rid = j["agent_contributions_run_id"]
    mine = [e for e in trail if e.get("run_id") == rid]
    for name, c in j["agent_contributions"].items():
        agent = {v: k for k, v in gen.AGENT.items()}[name]
        calls = [e["tool"] for e in mine if e["event_type"] == "tool_call_started" and e["actor"] == agent]
        assert sum(c["tool_calls"].values()) == len(calls)
        assert c["delegated_to"] == [gen.AGENT[(e.get("details") or {}).get("to")] for e in mine
                                     if e["event_type"] == "delegation_requested" and e["actor"] == agent]


@pytest.mark.parametrize("t", [101, 102, 103])
def test_f4_human_approvals_payments_and_drafts_match(data, db, trail, t):
    j = next(x for x in data["tickets"] if x["ticket_id"] == t)
    acts = {a["id"]: a for a in db["actions"]}
    decisions = [e for e in trail if e["event_type"] == "human_decision_finished" and (e.get("outcome") or {}).get("status") == "ok"
                 and ((e.get("details") or {}).get("ticket_id") == t
                      or acts.get((e.get("details") or {}).get("action_id"), {}).get("ticket_id") == t)]
    assert [a["trail_seq"] for a in j["human_approvals"]] == [e["seq"] for e in decisions]
    assert {p["payment_id"] for p in j["payments"]} == {a["payment_id"] for a in db["actions"] if a["ticket_id"] == t and a["payment_id"]}
    assert all(d["status"] == "draft" and d["sent"] is False for d in j["drafts"])
    assert [d["id"] for d in j["drafts"]] == [d["id"] for d in db["drafts"] if d["ticket_id"] == t]


def test_f5_evidence_kind_and_full_reply_limits_are_disclosed(data, desk):
    t101, t102, t103 = (next(x for x in data["tickets"] if x["ticket_id"] == t) for t in (101, 102, 103))
    assert all(r["full_agent_replies"] is None for r in t101["runs"])
    assert t102["runs"][0]["full_agent_replies"] and t103["runs"][0]["full_agent_replies"]
    assert "reconstructed after the run" in t103["runs"][0]["evidence_kind"]
    assert "saved by the read-only observer" in t102["runs"][0]["evidence_kind"]
    assert "recovery_record" in " ".join(t103["runs"][0]["evidence_files"])
    assert "only 300-character" in section(desk, 'id="actual-101"', "</section>")
    assert any("full agent replies" in l.lower() for l in data["limitations"])


# ---------------------------------------------------------------- cash

def test_c1_cash_reconciles_to_the_database(data, db, desk, board):
    g1 = json.loads((OUT / "p9_runs" / "g1_reset_record.json").read_text(encoding="utf-8"))
    opening = next(a["balance"] for a in g1["opening_balance_cash_accounts"] if a["name"] == "checking")
    assert opening == 3400.0
    assert round(opening - sum(p["amount"] for p in db["payments"]), 2) == db["cash"] == 160.0
    c = data["cash"]
    assert (c["opening_checking"], c["ending_checking"], c["cash_accounts_checking"], c["reconciles"]) == (3400.0, 160.0, 160.0, True)
    assert sorted((p["payment_id"], p["amount"]) for p in c["payments"]) == [(1, -2400.0), (2, -840.0)]
    assert c["quote_3_cash_effect"] == 0.0
    cash = section(desk, 'id="panel-cash"', "</section>")
    for s in ("$3,400.00", "-$2,400.00", "-$840.00", "$160.00", "generated no revenue"):
        assert s in cash
    assert "$160.00" in board and "no revenue" in board


# ---------------------------------------------------------------- screenshots

@pytest.mark.parametrize("t", [101, 102, 103])
def test_s1_screenshots_unchanged_and_embedded_byte_for_byte(board, data, t):
    png = (OUT / "p9_screenshots" / f"{t}_final.png").read_bytes()
    assert hashlib.sha256(png).hexdigest() == SCREENSHOT_SHA256[t]
    card = section(board, f'id="ticket-{t}"', "</section>")
    embedded = base64.b64decode(re.search(r'src="data:image/png;base64,([^"]+)"', card).group(1))
    assert embedded == png
    assert SCREENSHOT_SHA256[t] in card
    j = next(x for x in data["tickets"] if x["ticket_id"] == t)
    assert j["screenshot"]["sha256"] == SCREENSHOT_SHA256[t]


@pytest.mark.parametrize("t,label", [(101, "simulated workflow complete; fulfilment not verified"),
                                     (102, "business outcome confirmed"),
                                     (103, "simulated workflow complete; fulfilment not verified")])
def test_s2_board_labels_follow_the_records(board, t, label):
    card = section(board, f'id="ticket-{t}"', "</section>")
    assert f"RESOLVED · {label}" in card
    assert "<b>resolved</b>" in card


def test_s3_board_explains_both_requirement_statements_verbatim(board):
    prompts = (REAL_ROOT / "AI_prompts.md").read_text(encoding="utf-8")
    for q in (gen.REQ_P7, gen.REQ_P8):
        assert q in prompts and gen.html.escape(q) in board


# ---------------------------------------------------------------- no unsupported fulfilment claims

def test_n1_generated_text_makes_no_unsupported_fulfilment_or_acceptance_claims(desk, board, data):
    for t in (101, 102, 103):
        assert gen.unsupported_claims(gen.generated_only(section(desk, f'id="actual-{t}"', "</section>"))) == []
    assert gen.unsupported_claims(gen.generated_only(section(desk, 'id="panel-cash"', "</section>"))) == []
    assert gen.unsupported_claims(gen.generated_only(board)) == []
    for j in data["tickets"]:
        assert gen.unsupported_claims(j["outcome"]) == []
        assert gen.unsupported_claims(" ".join(j["differences_from_expected"])) == []


@pytest.mark.parametrize("sentence", ["The shirt was delivered to Tauhid Zaman.", "The Yale AI Club accepted the quote.",
                                      "The order was fulfilled on time.", "The hoodies shipped on Monday."])
def test_n2_the_claims_check_catches_unsupported_statements(sentence):
    assert gen.unsupported_claims(sentence) != []


@pytest.mark.parametrize("sentence", ["Delivery is not verified.", "Customer acceptance is not recorded.",
                                      "No record establishes that goods were delivered."])
def test_n3_the_claims_check_allows_negated_statements(sentence):
    assert gen.unsupported_claims(sentence) == []


def test_n4_simulated_tickets_never_state_business_outcome_confirmed(data):
    for j in data["tickets"]:
        if j["resolution"]["basis"] == "simulated_workflow_complete":
            assert j["resolution"]["business_outcome_confirmed"] is False
            assert {"delivery", "customer_acceptance"} <= set(j["resolution"]["unverified_outcomes"])
            assert "Not verified" in j["outcome"]


# ---------------------------------------------------------------- the generator never overwrites

def test_g1_generator_refuses_to_run_again():
    with pytest.raises(gen.BuildError):
        gen.main()


# ---------------------------------------------------------------- follow-up 53: wording and presentation corrections

def test_w1_template_is_the_unchanged_problem_6_file():
    raw = (OUT / "p6_desk_tickets_original.html").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == gen.TEMPLATE_SHA256
    text = raw.decode("utf-8")
    for t in (101, 102, 103):
        a = text.index(f'id="panel-{t}"')
        block = text[text.index('<div class="card expected-block">', a):text.index('<div class="card actual-block"', a)]
        assert hashlib.sha256(block.encode("utf-8")).hexdigest() == EXPECTED_BLOCK_SHA256[t]


def test_w2_not_verified_wording_and_title(desk, board):
    assert "by anyone" not in desk and "by anyone" not in board
    assert desk.count("Not verified in the system's records") == 2 and board.count("Not verified in the system's records") == 2
    assert "Plan (Problem 6) and Results (Problem 9)" in desk and "<h1>Campus Customs: Desk Tickets: Plan and Results</h1>" in desk
    assert ("Original Problem 6 description (kept as written): Homework 5, Problem 6: Plan the three tickets. This is the plan "
            "of what we <b>expect</b> the agent team to do, written before any live run.") in desk


def test_w3_ticket_102_is_described_as_a_legacy_record(desk, board, data):
    a102 = section(desk, 'id="actual-102"', "</section>")
    assert "Legacy record: audit.db event 9" in a102 and "no resolution-type field" in a102
    assert "resolution type recorded" not in a102
    assert "Legacy record: audit.db event 9" in section(board, 'id="ticket-102"', "</section>")
    j = {x["ticket_id"]: x for x in data["tickets"]}
    assert j[102]["resolution"]["basis_source"].startswith("legacy rule")
    assert all(j[t]["resolution"]["basis_source"] == "recorded resolution_basis field" for t in (101, 103))
    assert [a["resolution_basis_recorded"] for a in j[102]["human_approvals"] if a["operation"] == "resolve_ticket"] == [None]


def test_w4_run_wording_plural_and_error_presentation(desk):
    for bad in ("ended error", "ended completed", "audit.db events 8", "audit.db events 14", "status_code:"):
        assert bad not in desk, bad
    a101 = section(desk, 'id="actual-101"', "</section>")
    assert "Failed: the model provider returned HTTP 400 before any tool call" in a101
    assert "preserved unchanged in the audit trail (seq 4–8)" in a101
    # the original error text itself is still in the trail
    import audit_trail, paths
    ev = audit_trail.AuditTrail(paths.resolve(env={}, root=REAL_ROOT, use_dotenv=False).audit_trail_json).read()
    assert any("reasoning_effort are not supported" in json.dumps(e) for e in ev if e["seq"] in range(4, 9))


def test_w5_header_labels_have_readable_contrast(desk):
    assert "header code { background: rgba(255,255,255,.16); color: #fff;" in desk


def test_w6_planning_facts_are_labelled_historical_with_current_records(desk, db):
    assert desk.count("(historical: as of planning, 2026-10-10)") == 3
    for t in (101, 102, 103):
        head = desk[desk.index(f'id="panel-{t}"'):desk.index('<div class="card expected-block">', desk.index(f'id="panel-{t}"'))]
        assert "Records now (2026-10-11)" in head
    assert "invoice #501 is paid (payment #2, $840.00)" in desk and "rent payment #1 ($2,400.00) is recorded" in desk


# ---------------------------------------------------------------- Problem 10: the Reflection tab

def test_r1_reflection_is_the_source_fragment_verbatim_and_answers_all_five_questions(desk):
    frag = re.sub(r"\A\s*<!--.*?-->\s*", "", (OUT / "p10_reflection.html").read_text(encoding="utf-8"), flags=re.S).strip()
    refl = section(desk, 'id="panel-reflection"', "</section>")
    assert frag in refl
    for q in ("<h3>1. How would I evaluate the agents' performance on each ticket, and why?</h3>",
              "<h3>2. For each ticket, how did Actual compare with my Expected plan?</h3>",
              "<h3>3. What would have been simpler with one agent and tools, and why?</h3>",
              "<h3>4. Three new problems the current team could solve with the tools we built (hypothetical)</h3>",
              "<h3>5. Three new problems the current team cannot solve, and what it would take (hypothetical)</h3>"):
        assert q in refl, q
    for t in ("Ticket 101", "Ticket 102", "Ticket 103", "Cash tab"):
        assert t in refl, t


def test_r2_reflection_figures_match_the_records(desk, db, data):
    refl = section(desk, 'id="panel-reflection"', "</section>")
    assert db["cash"] == 160.0
    for s in ("$160", "−$840", "−$2,400", "$1,000", "51 model requests", "132,763 tokens", "43,787 tokens", "20,678 tokens"):
        assert s in refl, s
    runs = [r for t in data["tickets"] for r in t["runs"]]
    assert sum(r["usage"]["requests"] for r in runs) == 51
    assert sum(r["usage"]["input_tokens"] + r["usage"]["output_tokens"] for r in runs) == 132763
    assert gen.unsupported_claims(gen.generated_only(refl)) == []
    assert "saw Bulldog's block and still let the quote cover 20" not in refl        # follow-up 8 correction
