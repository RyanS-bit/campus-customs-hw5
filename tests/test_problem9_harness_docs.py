"""Problem 9, follow-up 49: output/harness.md section 13 agrees with the code, the schema, the records and the evidence.
READ-ONLY: reads the real harness, code, databases (mode=ro), audit trail and generated evidence.
"""
import json
import re
import sqlite3

import pytest

import main
import models
import paths
import server
import audit_trail
from conftest import REAL_ROOT

HARNESS = (REAL_ROOT / "output" / "harness.md").read_text(encoding="utf-8")
S13 = HARNESS[HARNESS.index("## 13. The final system"):]
AGENT_ROW = {"boss": "**Boss**", "inventory": "**Inventory**", "accounting": "**Accounting**",
             "facilities": "**Facilities**", "customer_service": "**Customer Service**"}
NAMES = {"boss": "Boss", "inventory": "Inventory", "accounting": "Accounting", "facilities": "Facilities",
         "customer_service": "Customer Service"}


def row(text, start):
    line = next(l for l in text.splitlines() if l.startswith(f"| {start}"))
    return [c.strip() for c in line.strip("|").split("|")]


def ro(name):
    return sqlite3.connect((REAL_ROOT / "data" / name).resolve().as_uri() + "?mode=ro", uri=True)


@pytest.mark.parametrize("agent", list(AGENT_ROW))
def test_d1_agent_tool_lists_match_the_server_access_matrix(agent):
    cells = row(S13[S13.index("### 13.2"):S13.index("### 13.3")], AGENT_ROW[agent])
    assert set(re.findall(r"`(\w+)`", cells[2])) == set(server.ACCESS[agent])


@pytest.mark.parametrize("tool", list(server.READ_TOOLS + server.WRITE_TOOLS))
def test_d2_tool_table_matches_kind_and_access(tool):
    cells = row(S13[S13.index("### 13.3"):S13.index("### 13.4")], f"`{tool}`")
    assert cells[1] == ("read" if tool in server.READ_TOOLS else "draft" if tool == "draft_message" else "propose")
    who = {a for a, tools in server.ACCESS.items() if tool in tools}
    named = cells[3]
    assert named == "all five" if len(who) == 5 else all(NAMES[a] in named for a in who)
    assert len(server.READ_TOOLS + server.WRITE_TOOLS) == 10 and "**Ten** tools" in S13


def test_d3_limits_model_and_routes_match_the_code():
    b = models.RunBudget().describe()
    text = S13[S13.index("**Limits per run**"):]
    for v in (f"{b['request_limit']} model requests", f"{b['total_tokens_limit']:,} tokens", f"{b['tool_calls_limit']} tool calls",
              f"{b['max_output_tokens']:,} output tokens", f"{b['max_delegations']} delegations", f"depth {b['max_depth']}",
              f"{int(b['wall_clock_seconds'])} s", f"{int(b['request_timeout_seconds'])} s request timeout", f"{b['max_http_retries']} retry"):
        assert v in text, v
    assert '{"openai_reasoning_effort": "none"}' in S13
    api = [r for r in main.create_app(p=paths.resolve(env={}, root=REAL_ROOT, use_dotenv=False)).routes
           if getattr(r, "path", "").startswith("/api/")]
    assert sum(len(r.methods) for r in api) == 14 and "14 API routes" in S13


def test_d4_extension_schema_matches():
    con = ro("campus_customs_new.db")
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"pending_actions", "drafts", "schema_ext_meta", "reset_stamp"} <= tables
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'pending_actions'").fetchone()[0]
    for status in ("proposed", "approved", "rejected", "executed", "failed"):
        assert f"'{status}'" in sql and status in S13
    assert con.execute("SELECT version FROM schema_ext_meta").fetchone()[0] == 2 and "version 2" in S13
    a = ro("audit.db")
    assert {"events", "audit_meta"} <= {r[0] for r in a.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_d5_run_and_cash_facts_match_the_records():
    trail = audit_trail.AuditTrail(REAL_ROOT / "output" / "audit_trail.json").read()
    runs = {e["run_id"][:8]: ((e.get("details") or {}).get("ticket_id"), None) for e in trail if e["event_type"] == "run_started"}
    status = {e["run_id"][:8]: e["outcome"]["run_status"] for e in trail if e["event_type"] == "run_finished"}
    for rid, ticket, st in (("53e4f001", 101, "error"), ("ae8d9bf4", 101, "completed"), ("84270c9b", 101, "completed"),
                            ("f2412172", 102, "completed"), ("fa38a75b", 103, "completed")):
        assert runs[rid][0] == ticket and status[rid] == st and f"`{rid}…`" in S13
    assert len(runs) == 5
    con = ro("campus_customs_new.db")
    assert con.execute("SELECT balance FROM cash_accounts WHERE name = 'checking'").fetchone()[0] == 160.0
    assert sorted(con.execute("SELECT id, kind, amount FROM payments")) == [(1, "lease", 2400.0), (2, "invoice", 840.0)]
    assert "$3,400.00 − $2,400.00 (payment 1, ticket 102) − $840.00 (payment 2, ticket 101) = **$160.00**" in S13
    resolved = json.loads((REAL_ROOT / "output" / "resolved_tickets.json").read_text(encoding="utf-8"))
    bases = {t["ticket_id"]: t["resolution"]["basis"] for t in resolved["tickets"]}
    assert bases == {101: "simulated_workflow_complete", 102: "business_outcome", 103: "simulated_workflow_complete"}


def test_d6_ticket_102_legacy_record_is_described_exactly():
    a = ro("audit.db")
    eid, payload = a.execute("SELECT id, payload FROM events WHERE event_type = 'ticket_resolved' AND ref_id = 102").fetchone()
    d = json.loads(payload)
    assert eid == 9 and "resolution_basis" not in d and "unverified_outcomes" not in d and d["business_outcome_confirmed"] is True
    listed = S13[S13.index("payload keys"):].split("\n", 1)[0]
    assert set(re.findall(r"`(\w+)`", listed)) == set(d)
    assert d["basis"] == "the person's own confirmation (attribution only)" and d["basis"] in S13
    first_with_field = a.execute("SELECT id, ts_utc FROM events WHERE event_type = 'ticket_resolved' AND "
                                 "payload LIKE '%resolution_basis%' ORDER BY id").fetchone()
    assert first_with_field[0] == 15 and "19:17:18 UTC (`audit.db` event 15)" in S13


def test_d7_stale_statements_are_gone():
    assert "**No live run yet.**" not in HARNESS
    for f in ("p9_preflight_plan.md", "p9_ticket101_assessment.md", "harness.md", "design.md"):
        text = (REAL_ROOT / "output" / f).read_text(encoding="utf-8")
        assert not re.search(r"\b(he|his|him|she|her|hers)\b", text), f
