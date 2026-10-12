"""Build the Problem 9 deliverables from the authentic records (Problem 9, follow-up 47).

Reads only (never writes) these sources:
  data/campus_customs_new.db and data/audit.db      (SQLite, mode=ro)
  output/audit_trail.json                            (append-only, hash-chained; verified on read)
  output/run_responses/*.jsonl                       (full agent replies, where they exist)
  output/p9_runs/                                    (observer, recovery and gate records)
  output/p9_screenshots/                             (three unedited PNGs and capture_record.json)

Template: output/p6_desk_tickets_original.html, the Problem 6 file exactly as it was before Problem 9 (SHA-256 checked).

Writes:
  output/desk_tickets.html   built from the template: only the three Actual placeholders, the Cash tab placeholder, the three
                             "Status" cells of the request tables, the header meta line and the footer.
                             The three Expected blocks must stay byte-for-byte identical, or nothing is written.
  output/resolved_tickets.json
  output/resolved_board.html  the three PNGs embedded as base64 of their exact bytes

It stops (writes nothing) if: an Expected block would change; the cash does not reconcile to cash_accounts;
a screenshot's SHA-256 differs from capture_record.json; a ticket is not resolved; or the audit trail fails
verification. Nothing here contacts a model, the network or the backend.

Run from hw5/:  .venv/Scripts/python scripts/build_p9_evidence.py
Problem 10: if output/p10_reflection.html exists, its content (without its leading comment) replaces the Reflection
placeholder. --only-desk builds and checks all three outputs but writes only desk_tickets.html.
Replacing existing outputs (follow-up 53) needs  --replace-preserved <folder>  where the folder holds byte-identical
copies of the current outputs and their SHA256SUMS.txt. All three files are built and checked first, written to
temporary files, and only then moved into place; the previous versions stay in that folder.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

HW5 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HW5 / "mcp_server"))
import audit_trail  # noqa: E402
import paths  # noqa: E402
import run_responses  # noqa: E402

OUT = HW5 / "output"
RUNS_DIR, SHOTS_DIR = OUT / "p9_runs", OUT / "p9_screenshots"
DESK, JSON_OUT, BOARD = OUT / "desk_tickets.html", OUT / "resolved_tickets.json", OUT / "resolved_board.html"
TEMPLATE = OUT / "p6_desk_tickets_original.html"
REFLECTION = OUT / "p10_reflection.html"           # Problem 10: the reflection's source fragment (inserted if present)
REFLECTION_PLACEHOLDER = ('<h2>Reflection</h2>\n  <div class="card later" data-section="coming-later"><div class="big">Coming later'
                          '</div><div>This tab will be completed in a later problem.</div></div>')
TEMPLATE_SHA256 = "da8e8b715e82913abc85fb8aaef2a781c0f58a4d19bdd897058079c80a20e145"
TICKETS = (101, 102, 103)
AGENT = {"boss": "Boss", "inventory": "Inventory", "accounting": "Accounting", "facilities": "Facilities",
         "customer_service": "Customer Service", "system": "System"}
WRITE_TOOLS = ("propose_payment", "propose_purchase", "propose_quote", "draft_message")
UNVERIFIED = {"delivery": "physical production, shipment, delivery or receipt of goods",
              "customer_acceptance": "acceptance by the customer or counterparty",
              "message_sent": "that any message was actually sent (drafts are never sent, R7)",
              "outside_arrangement": "any payment, refund or arrangement made outside this system"}
# The Expected plans' routing, as written in the Expected sections (Problem 6), used only to list differences.
EXPECTED = {
    101: {"first": "inventory", "handoffs": [("boss", "inventory"), ("inventory", "accounting")],
          "optional": [("boss", "customer_service")], "not_involved": ["facilities"],
          "writes": [("accounting", "propose_payment")], "optional_writes": [("customer_service", "draft_message")]},
    102: {"first": "facilities", "handoffs": [("boss", "facilities"), ("facilities", "accounting")],
          "optional": [], "not_involved": ["inventory", "customer_service"],
          "writes": [("accounting", "propose_payment"), ("facilities", "draft_message")], "optional_writes": []},
    103: {"first": "customer_service", "handoffs": [("boss", "customer_service"), ("customer_service", "inventory")],
          "optional": [("customer_service", "accounting"), ("inventory", "accounting")], "not_involved": ["facilities"],
          "writes": [("customer_service", "propose_quote"), ("customer_service", "draft_message")],
          "optional_writes": [("inventory", "propose_purchase")]},
}
LEGACY_NOTE = ("Legacy record: audit.db event {event} has business_outcome_confirmed: true and no resolution-type "
               "field (it predates that field, added in follow-up 36). At the time, confirming the business outcome was "
               "the only way to resolve, so it is read as business_outcome. The stored record was not changed (harness §13.10).")
TITLE_OLD = "<title>Campus Customs: Desk Tickets Plan (Homework 5, Problem 6)</title>"
TITLE_NEW = "<title>Campus Customs: Desk Tickets: Plan (Problem 6) and Results (Problem 9), Homework 5</title>"
H1_OLD = "<h1>Campus Customs: Desk Tickets Plan</h1>"
H1_NEW = "<h1>Campus Customs: Desk Tickets: Plan and Results</h1>"
LEDE_OLD = ("<p>Homework 5, Problem 6: Plan the three tickets. This is the plan of what we <b>expect</b> the agent team to do, "
            "written before any live run.</p>")
LEDE_NEW = ("<p>This file has two parts. The <b>Expected</b> sections are the Problem 6 plan, unchanged. The <b>Actual</b> "
            "sections and the <b>Cash</b> tab are the Problem 9 results, generated from the recorded evidence after the live "
            "runs.</p>\n  <p>Original Problem 6 description (kept as written): Homework 5, Problem 6: Plan the three tickets. "
            "This is the plan of what we <b>expect</b> the agent team to do, written before any live run.</p>")
FACTS_OLD = "<h3>Verified facts the agents will find</h3>"
FACTS_NEW = ('<h3>Verified facts the agents will find <span class="hist">(historical: as of planning, 2026-10-10)</span></h3>\n'
             '      <p class="hist-note">{now}</p>')

# The two requirement statements about resolution, quoted verbatim from AI_prompts.md (checked below).
REQ_P7 = "Do not automatically mark a ticket resolved merely because the agent run finishes."
REQ_P8 = "marks tickets resolved when runs finish."
NEGATION = re.compile(r"\b(not|no|never|unverified|without|whether|cannot|n't|pending|unknown|nothing)\b", re.I)
CLAIM = re.compile(r"\b(delivered|shipped|received by|accepted|fulfil+ed|fulfil+ment)\b", re.I)


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def esc(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def money(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def utc(ts: str | None) -> str:
    return (ts or "")[:19].replace("T", " ") + (" UTC" if ts else "")


def ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- gather (read-only)

def gather() -> dict:
    p = paths.resolve()
    trail = audit_trail.AuditTrail(p.audit_trail_json).read()          # raises if the chain does not verify
    with closing(ro(p.working_db)) as w, closing(ro(p.audit_db)) as a:
        db = {
            "tickets": {r["id"]: dict(r) for r in w.execute("SELECT * FROM tickets")},
            "payments": [dict(r) for r in w.execute("SELECT * FROM payments ORDER BY id")],
            "actions": [dict(r) for r in w.execute("SELECT * FROM pending_actions ORDER BY id")],
            "drafts": [dict(r) for r in w.execute("SELECT * FROM drafts ORDER BY id")],
            "cash": [dict(r) for r in w.execute("SELECT * FROM cash_accounts")],
            "invoices": {r["id"]: dict(r) for r in w.execute("SELECT * FROM invoices")},
            "leases": {r["id"]: dict(r) for r in w.execute("SELECT * FROM leases")},
            "inventory": [dict(r) for r in w.execute("SELECT * FROM inventory")],
            "audit_events": [dict(r) for r in a.execute("SELECT id, ts_utc, actor, event_type, ref_table, ref_id, call_id, "
                                                        "payload FROM events ORDER BY id")],
        }
    resolutions = {}
    for e in db["audit_events"]:
        if e["event_type"] == "ticket_resolved":
            d = json.loads(e["payload"])
            resolutions[e["ref_id"]] = {
                "basis": d.get("resolution_basis") or ("business_outcome" if d.get("business_outcome_confirmed") else None),
                "business_outcome_confirmed": bool(d.get("business_outcome_confirmed")),
                "workflow_complete_confirmed": d.get("workflow_complete_confirmed"),
                "unverified_outcomes": d.get("unverified_outcomes") or [],
                "resolved_by": d.get("decided_by"), "resolved_at": e["ts_utc"], "summary": d.get("resolution_summary"),
                "basis_recorded": d.get("resolution_basis") is not None,
                "completed_runs_cited": d.get("completed_runs") or [], "audit_db_event_id": e["id"],
                "decision_id": e["call_id"]}
    g1 = json.loads((RUNS_DIR / "g1_reset_record.json").read_text(encoding="utf-8"))
    capture = json.loads((SHOTS_DIR / "capture_record.json").read_text(encoding="utf-8"))
    return {"paths": p, "trail": trail, "db": db, "resolutions": resolutions, "g1": g1, "capture": capture}


def run_summary(trail: list[dict], run_id: str, p) -> dict:
    mine = [e for e in trail if e.get("run_id") == run_id]
    started = next(e for e in mine if e["event_type"] == "run_started")
    finished = next((e for e in mine if e["event_type"] == "run_finished"), None)
    delegations = [{"seq": e["seq"], "time": e["ts_utc"], "from": e["actor"], "to": (e.get("details") or {}).get("to"),
                    "task": (e.get("details") or {}).get("task"), "chain": e.get("chain")}
                   for e in mine if e["event_type"] == "delegation_requested"]
    refused_delegations = [e for e in mine if e["event_type"] == "delegation_refused"]
    calls, finished_by_id = [], {e.get("call_id"): e for e in mine if e["event_type"] == "tool_call_finished"}
    for e in mine:
        if e["event_type"] == "tool_call_started":
            f = finished_by_id.get(e.get("call_id")) or {}
            calls.append({"seq": e["seq"], "agent": e["actor"], "tool": e["tool"],
                          "status": (f.get("outcome") or {}).get("status"), "code": (f.get("outcome") or {}).get("code"),
                          "links": f.get("links") or {}})
    agents = sorted({e["actor"] for e in mine if e.get("actor_kind") == "agent"}, key=list(AGENT).index)
    rfile = run_responses.responses_path(p.audit_trail_json, run_id)
    errors = [e for e in mine if e["event_type"] == "error"]
    return {"run_id": run_id, "ticket_id": (started.get("details") or {}).get("ticket_id"),
            "started": started["ts_utc"], "finished": finished["ts_utc"] if finished else None,
            "run_status": (finished.get("outcome") or {}).get("run_status") if finished else "unfinished",
            "usage": finished.get("usage") if finished else None, "events": len(mine),
            "seq_range": [mine[0]["seq"], mine[-1]["seq"]], "model": (started.get("details") or {}).get("model"),
            "agents": agents, "delegations": delegations, "refused_delegations": len(refused_delegations),
            "tool_calls": calls, "error": ((errors[0].get("outcome") or {}).get("message") if errors else None),
            "full_responses_file": f"output/run_responses/{rfile.name}" if rfile.exists() else None}


def run_evidence(run_id: str, ticket: int) -> dict:
    """Which observer files exist for this run, and whether they were captured during the run or reconstructed."""
    # The observer names a run's files <prefix>run.json, <prefix>events.json, ...; the run file identifies the run.
    prefix = None
    for f in sorted(RUNS_DIR.glob(f"{ticket}_*run.json")):
        if json.loads(f.read_text(encoding="utf-8")).get("run", {}).get("run_id") == run_id:
            prefix = f.name[: -len("run.json")]
    suffixes = ("run.json", "events.json", "responses.json", "after_run_state.json", "recovery_record.json")
    files = [prefix + s for s in suffixes if prefix and (RUNS_DIR / (prefix + s)).exists()]
    incident = sorted(f.name for f in RUNS_DIR.glob(f"{ticket}_*incident*.md") if run_id in f.read_text(encoding="utf-8"))
    reconstructed = any("recovery_record" in n for n in files)
    return {"observer_files": [f"output/p9_runs/{n}" for n in files + incident],
            "observer_files_kind": ("reconstructed after the run from the trail, the response file and the database "
                                    "(no observer was running; see the recovery record)") if reconstructed
                                   else "saved by the read-only observer while and right after the run",
            "reconstructed": reconstructed}


def differences(ticket: int, run: dict, all_runs: list[dict]) -> list[str]:
    exp = EXPECTED[ticket]
    edges = [(d["from"], d["to"]) for d in run["delegations"]]
    first = next((d["to"] for d in run["delegations"] if d["from"] == "boss"), None)
    writes = [(c["agent"], c["tool"]) for c in run["tool_calls"] if c["tool"] in WRITE_TOOLS and c["status"] == "ok"]
    out = []
    if len(all_runs) > 1:
        out.append(f"{len(all_runs)} runs were needed; the plan assumed one (see the attempts listed above).")
        for i, r in enumerate(all_runs, 1):
            if r is run:
                continue
            made = [c["tool"] for c in r["tool_calls"] if c["tool"] in WRITE_TOOLS and c["status"] == "ok"]
            if r["error"]:
                out.append(f"Attempt {i} failed before any tool call (a model-provider configuration error, "
                           "corrected offline; harness §12.1).")
            elif made:
                out.append(f"Attempt {i} completed and created: {', '.join(made)}.")
            else:
                out.append(f"Attempt {i} completed without creating any proposal or draft.")
        out.append(f"The comparison below is for the final attempt ({all_runs.index(run) + 1}).")
    if first == exp["first"]:
        out.append(f"First contact matched the plan: the Boss delegated first to {AGENT[first]}.")
    else:
        out.append(f"First contact differed: the plan named {AGENT[exp['first']]}; the Boss delegated first to "
                   f"{AGENT.get(first, first)}.")
    for a, b in exp["handoffs"]:
        if (a, b) in edges:
            out.append(f"Planned hand-off {AGENT[a]} → {AGENT[b]} happened.")
        else:
            instead = [f"{AGENT[x]} → {AGENT[y]}" for x, y in edges if y == b] or ["none"]
            out.append(f"Planned hand-off {AGENT[a]} → {AGENT[b]} did not happen (delegations to {AGENT[b]}: "
                       f"{', '.join(instead)}).")
    for a, b in exp["optional"]:
        if (a, b) in edges:
            out.append(f"Optional hand-off {AGENT[a]} → {AGENT[b]} was used.")
    extra = [f"{AGENT[x]} → {AGENT[y]}" for x, y in edges if (x, y) not in exp["handoffs"] + exp["optional"]]
    if extra:
        out.append("Delegations not in the plan: " + "; ".join(sorted(Counter(extra).keys())) +
                   (f" ({sum(Counter(extra).values())} in total)" if len(extra) > 1 else "") + ".")
    for a in exp["not_involved"]:
        out.append(f"{AGENT[a]} was {'involved, contrary to' if a in run['agents'] else 'not involved, as'} planned.")
    for a, tool in exp["writes"]:
        out.append(f"Planned {tool} by {AGENT[a]}: {'done' if (a, tool) in writes else 'not done'}.")
    for a, tool in exp["optional_writes"]:
        if (a, tool) in writes:
            out.append(f"Optional {tool} by {AGENT[a]}: done.")
        else:
            out.append(f"Optional {tool} by {AGENT[a]}: not done.")
    other = [f"{tool} by {AGENT[a]}" for a, tool in writes if (a, tool) not in exp["writes"] + exp["optional_writes"]]
    if other:
        out.append("Records created outside the plan: " + ", ".join(other) + ".")
    refused = [c for c in run["tool_calls"] if c["status"] not in ("ok", None)]
    for c in refused:
        out.append(f"{AGENT[c['agent']]}'s {c['tool']} call was {c['status']} ({c['code']}); nothing was created by it.")
    return out


# ---------------------------------------------------------------- model of each ticket

def build_model(src: dict) -> dict:
    db, trail, p = src["db"], src["trail"], src["paths"]
    runs_by_ticket = defaultdict(list)
    for e in trail:
        if e["event_type"] == "run_started":
            s = run_summary(trail, e["run_id"], p)
            s["evidence"] = run_evidence(e["run_id"], s["ticket_id"])
            runs_by_ticket[s["ticket_id"]].append(s)
    actions = {a["id"]: a for a in db["actions"]}
    decisions = []
    for e in trail:
        if e["event_type"] == "human_decision_finished" and (e.get("outcome") or {}).get("status") == "ok":
            d = e.get("details") or {}
            ticket = d.get("ticket_id") or (actions[d["action_id"]]["ticket_id"] if d.get("action_id") else None)
            decisions.append({"ticket_id": ticket, "operation": d.get("operation"), "action_id": d.get("action_id"),
                              "result": d.get("result"), "by": e["actor"].removeprefix("human:"), "at": e["ts_utc"],
                              "trail_seq": e["seq"], "decision_id": e.get("call_id"),
                              "payment_id": (e.get("links") or {}).get("payment_id"),
                              "resolution_basis": d.get("resolution_basis")})
    tickets = {}
    for t in TICKETS:
        row = db["tickets"][t]
        if row["status"] != "resolved" or t not in src["resolutions"]:
            raise BuildError(f"ticket {t} is not resolved in the database and audit.db")
        runs = runs_by_ticket[t]
        final = [r for r in runs if r["run_status"] == "completed"][-1]
        tickets[t] = {"row": row, "runs": runs, "final_run": final, "resolution": src["resolutions"][t],
                      "decisions": [d for d in decisions if d["ticket_id"] == t],
                      "actions": [a for a in db["actions"] if a["ticket_id"] == t],
                      "drafts": [d for d in db["drafts"] if d["ticket_id"] == t],
                      "payments": [pm for pm in db["payments"] if any(a["payment_id"] == pm["id"] and a["ticket_id"] == t
                                                                       for a in db["actions"])],
                      "differences": differences(t, final, runs)}
    # cash reconciliation: opening balance recorded right after the G1 reset; only executed payments change it
    opening = next(a["balance"] for a in src["g1"]["opening_balance_cash_accounts"] if a["name"] == "checking")
    stored = next(c["balance"] for c in db["cash"] if c["name"] == "checking")
    lines = []
    for pm in db["payments"]:
        act = next(a for a in db["actions"] if a["payment_id"] == pm["id"])
        ev = [e["id"] for e in db["audit_events"] if e["event_type"] == "payment_executed" and e["ref_id"] == pm["id"]]
        lines.append({"ticket_id": act["ticket_id"], "payment_id": pm["id"], "kind": pm["kind"], "ref_id": pm["ref_id"],
                      "amount": -pm["amount"], "proposal_id": act["id"], "approved_by": pm["approved_by"],
                      "executed_at": act["executed_at"], "paid_at_shop_date": pm["paid_at"], "audit_db_event_ids": ev})
    ending = round(opening + sum(l["amount"] for l in lines), 2)
    if ending != round(stored, 2):
        raise BuildError(f"cash does not reconcile: {opening} + payments = {ending}, cash_accounts = {stored}")
    quotes = [a for a in db["actions"] if a["kind"] == "quote"]
    return {"tickets": tickets, "cash": {"opening": opening, "opening_source": "output/p9_runs/g1_reset_record.json "
                                         "(opening_balance_cash_accounts, read right after the controlled reset)",
                                         "lines": lines, "ending": ending, "stored": stored, "quotes": quotes,
                                         "reset_at": src["g1"]["reset_response"].get("result", {}).get("finished_utc")
                                         if isinstance(src["g1"]["reset_response"].get("result"), dict) else None},
            "decisions": decisions}


# ---------------------------------------------------------------- desk_tickets.html

def actual_html(t: int, tk: dict, capture: dict) -> str:
    res, row = tk["resolution"], tk["row"]
    basis_label = ("Business outcome confirmed" if res["basis"] == "business_outcome"
                   else "Simulated workflow complete; fulfilment not verified")
    h = [f'<div class="actual-done" id="actual-{t}">']
    h.append(f'<p><b>Final status:</b> {esc(row["status"])} &nbsp;·&nbsp; <b>Resolution type:</b> {esc(basis_label)} '
             f'(<code>{esc(res["basis"])}</code>) &nbsp;·&nbsp; by {esc(res["resolved_by"])}, {esc(utc(res["resolved_at"]))}</p>')
    if not res["basis_recorded"]:
        h.append(f'<p class="legacy">{esc(LEGACY_NOTE.format(event=res["audit_db_event_id"]))}</p>')
    if res["basis"] != "business_outcome":
        h.append("<p><b>Not verified in the system's records:</b> " + "; ".join(esc(UNVERIFIED[u]) for u in res["unverified_outcomes"])
                 + ". The person confirmed only that the desk's simulated workflow is complete.</p>")
    h.append('<p class="small">The person\'s own resolution summary, as recorded in <code>data/audit.db</code> '
             f'(event {res["audit_db_event_id"]}):</p><blockquote class="record">{esc(res["summary"])}</blockquote>')
    # runs
    h.append("<h4>Agent runs</h4><table><tr><th>Run</th><th>When (UTC)</th><th>Outcome</th><th>Usage</th>"
             "<th>Evidence</th></tr>")
    for i, r in enumerate(tk["runs"], 1):
        u = r["usage"] or {}
        if r["error"]:
            code = re.search(r"status_code: (\d+)", r["error"])
            incident = [f.split("/")[-1] for f in r["evidence"]["observer_files"] if f.endswith(".md")]
            outcome = ("Failed: the model provider returned "
                       + (f"HTTP {code.group(1)}" if code else "an error")
                       + " before any tool call. The original error text is preserved unchanged in the audit trail "
                       + f"(seq {r['seq_range'][0]}–{r['seq_range'][1]})"
                       + (f" and in <code>{esc(incident[0])}</code>" if incident else "") + ".")
        else:
            outcome = esc({"completed": "Completed", "stopped": "Stopped at a limit"}.get(r["run_status"], r["run_status"]))
        ev = "<br>".join(f"<code>{esc(f.split('/')[-1])}</code>" for f in r["evidence"]["observer_files"])
        kind = esc(r["evidence"]["observer_files_kind"])
        full = (f"Full agent replies: <code>{esc(r['full_responses_file'].split('/')[-1])}</code>" if r["full_responses_file"]
                else "Full agent replies: not stored (this run predates the full-response recorder; only 300-character "
                     "summaries exist in the trail)")
        h.append(f"<tr><td>Attempt {i}<br><code>{esc(r['run_id'][:8])}…</code></td><td>{esc(utc(r['started']))}<br>to "
                 f"{esc(utc(r['finished'])[11:])}</td><td>{outcome}</td><td>{u.get('requests', 0)} requests, "
                 f"{u.get('tool_calls', 0)} tool calls, {u.get('input_tokens', 0) + u.get('output_tokens', 0):,} tokens; "
                 f"{r['events']} trail events (seq {r['seq_range'][0]}–{r['seq_range'][1]})</td>"
                 f"<td>{ev}<br><span class='small'>{kind}. {full}.</span></td></tr>")
    h.append("</table>")
    # delegations and tools of each run that did work
    for i, r in enumerate(tk["runs"], 1):
        if not r["delegations"] and not r["tool_calls"]:
            continue
        h.append(f"<h4>Attempt {i} (<code>{esc(r['run_id'][:8])}…</code>): delegations and tool calls</h4><ol class='dlist'>")
        for d in r["delegations"]:
            h.append(f"<li>{esc(utc(d['time'])[11:19])} · <b>{esc(AGENT[d['from']])} → {esc(AGENT[d['to']])}</b>: "
                     f"<span class='small'>{esc(d['task'])}</span></li>")
        h.append("</ol><table><tr><th>Agent</th><th>Tools called (count)</th><th>Records created or refused</th></tr>")
        by_agent = defaultdict(list)
        for c in r["tool_calls"]:
            by_agent[c["agent"]].append(c)
        for a in [x for x in AGENT if x in by_agent]:
            cs = by_agent[a]
            counts = ", ".join(f"<code>{esc(k)}</code>×{v}" for k, v in Counter(c["tool"] for c in cs).items())
            made = [f"{c['tool']} → " + ", ".join(f"{k} {v}" for k, v in c["links"].items() if k in ("action_id", "draft_id"))
                    for c in cs if c["tool"] in WRITE_TOOLS and c["status"] == "ok"]
            made += [f"{c['tool']} {c['status']} ({c['code']})" for c in cs if c["status"] not in ("ok", None)]
            h.append(f"<tr><td>{esc(AGENT[a])}</td><td>{counts}</td><td>{esc('; '.join(made) or '—')}</td></tr>")
        h.append("</table>")
    # human decisions
    h.append("<h4>Human decisions (from the audit trail)</h4><ul class='tight'>")
    for d in tk["decisions"]:
        what = {"approve": f"approved proposal #{d['action_id']} ({d['result']}"
                           + (f", payment #{d['payment_id']}" if d.get("payment_id") else "") + ")",
                "resolve_ticket": (f"resolved ticket {t} (resolution type recorded: {d['resolution_basis']})"
                                   if d["resolution_basis"] else
                                   f"resolved ticket {t}, confirming the business outcome (business_outcome_confirmed: "
                                   "true). This record has no resolution-type field; it is read as business_outcome "
                                   "(harness §13.10)"),
                "reject": f"rejected proposal #{d['action_id']}", "execute_purchase": f"executed purchase #{d['action_id']}"
                }.get(d["operation"], d["operation"])
        h.append(f"<li>{esc(utc(d['at']))}: {esc(d['by'])} {esc(what)} (trail seq {d['trail_seq']})</li>")
    h.append("</ul>")
    # records
    h.append("<h4>Business records for this ticket (database)</h4><ul class='tight'>")
    for a in tk["actions"]:
        h.append(f"<li>Proposal #{a['id']}: {esc(a['kind'])}, {esc(money(a['amount']))}, status <b>{esc(a['status'])}</b>"
                 + (f", payment #{a['payment_id']}" if a["payment_id"] else "")
                 + (" — an internal price approval: no revenue, no cash, nothing sent (R6, R7)" if a["kind"] == "quote" else "")
                 + "</li>")
    for d in tk["drafts"]:
        h.append(f"<li>Draft #{d['id']} to {esc(d['recipient'])}: <b>unsent</b> (created {esc(utc(d['created_at']))}; "
                 "the system never sends messages, R7)</li>")
    h.append("</ul>")
    # differences
    h.append("<h4>Actual compared with Expected</h4><ul class='tight'>"
             + "".join(f"<li>{esc(x)}</li>" for x in tk["differences"]) + "</ul>")
    shot = next(s for s in capture["shots"] if s["ticket_id"] == t)
    h.append(f"<p class='small'>Final-state dashboard screenshot: <code>{esc(shot['file'])}</code> (SHA-256 "
             f"<code>{esc(shot['sha256'][:16])}…</code>, captured {esc(utc(shot['captured_utc']))}); shown in "
             "<code>output/resolved_board.html</code>.</p></div>")
    return "\n".join(h)


def cash_html(cash: dict) -> str:
    h = ['<div data-section="cash-reconciliation">',
         '<p class="lede">Checking balance from the controlled reset to the end of the three-ticket run. Only executed '
         'payments change cash; proposals, approved quotes and estimates never do (R1, R6).</p>',
         '<div class="card"><table><tr><th>Step</th><th>Ticket</th><th>Transaction</th><th>Change</th><th>Balance</th></tr>',
         f'<tr><td>Opening balance after the reset</td><td>—</td><td>Recorded in <code>{esc(cash["opening_source"].split(" ")[0])}</code>'
         f'</td><td>—</td><td><b>{esc(money(cash["opening"]))}</b></td></tr>']
    bal = cash["opening"]
    order = sorted(cash["lines"], key=lambda l: l["executed_at"])
    for l in order:
        bal = round(bal + l["amount"], 2)
        what = ("Rent, lease #1 (Elm City Properties)" if l["kind"] == "lease" else f"Invoice #{l['ref_id']} (Bulldog Print Co)")
        h.append(f"<tr><td>Payment #{l['payment_id']}</td><td>{l['ticket_id']}</td><td>{esc(what)}; proposal #{l['proposal_id']}, "
                 f"approved and executed by {esc(l['approved_by'])} at {esc(utc(l['executed_at']))}; audit.db "
                 f"event{'s' if len(l['audit_db_event_ids']) != 1 else ''} {', '.join(map(str, l['audit_db_event_ids']))}</td><td>{esc(money(l['amount']))}</td><td>{esc(money(bal))}</td></tr>")
    for q in cash["quotes"]:
        h.append(f"<tr><td>Quote #{q['id']}</td><td>{q['ticket_id']}</td><td>{esc(money(q['amount']))} quote, status "
                 f"<b>{esc(q['status'])}</b>: an internal price approval only. It generated no revenue and moved no cash "
                 "(R6); nothing was sent to the customer (R7); customer acceptance is not recorded.</td><td>$0.00</td>"
                 f"<td>{esc(money(bal))}</td></tr>")
    h.append(f"<tr><td><b>Ending balance</b></td><td>—</td><td>Equals <code>cash_accounts</code> (checking) in the working "
             f"database: {esc(money(cash['stored']))}</td><td>{esc(money(cash['ending'] - cash['opening']))}</td>"
             f"<td><b>{esc(money(cash['ending']))}</b></td></tr></table></div>")
    by_ticket = defaultdict(float)
    for l in cash["lines"]:
        by_ticket[l["ticket_id"]] += l["amount"]
    h.append("<div class='card'><h3>Cash by ticket</h3><ul class='tight'>"
             + "".join(f"<li>Ticket {t}: {esc(money(by_ticket.get(t, 0.0)))}" + (" (no payment; quote approved internally, no revenue)"
                                                                              if t == 103 else "") + "</li>" for t in TICKETS)
             + f"</ul><p>{esc(money(cash['opening']))} − $2,400.00 − $840.00 = <b>{esc(money(cash['ending']))}</b>. "
               "These are recorded payments within the simulated Campus Customs system; no real bank transfer occurred.</p></div></div>")
    return "\n".join(h)


EXTRA_CSS = """  header code { background: rgba(255,255,255,.16); color: #fff; border: 1px solid rgba(255,255,255,.35); }
  .hist { font-weight: 400; font-size: 12.5px; color: var(--muted); }
  .hist-note { margin: 0 0 8px; padding: 6px 10px; border-radius: 6px; background: #f3f5fa; font-size: 13px; color: #2c3a52; }
  .legacy { font-size: 13.5px; background: #f3f5fa; border-left: 3px solid #8a94a8; padding: 6px 10px; }
  .actual-done h4 { margin: 14px 0 6px; font-size: 14.5px; color: var(--actual); }
  .actual-done .small, .small { font-size: 12.5px; color: var(--muted); }
  blockquote.record { margin: 6px 0 10px; padding: 8px 12px; background: #fff; border-left: 3px solid #e0b45e; font-size: 13.5px; }
  ol.dlist { margin: 4px 0 8px; padding-left: 20px; font-size: 13.5px; }
"""


def now_notes(src: dict) -> dict:
    """One line per ticket: the records NOW, next to the historical planning facts (from the database)."""
    db = src["db"]
    inv501 = db["invoices"][501]
    pay501 = next((p for p in db["payments"] if p["kind"] == "invoice" and p["ref_id"] == 501), None)
    pay_rent = next((p for p in db["payments"] if p["kind"] == "lease" and p["ref_id"] == 1), None)
    qty = {(r["sku"], r["size"]): r["qty"] for r in db["inventory"]}
    quote = next((a for a in db["actions"] if a["kind"] == "quote" and a["ticket_id"] == 103), None)
    purchases = [a for a in db["actions"] if a["kind"] == "purchase"]
    open_bulldog = [i for i in db["invoices"].values() if i["vendor_id"] == 1 and i["status"] == "open"]
    return {
        101: (f"Records now (2026-10-11): invoice #501 is {inv501['status']}"
              + (f" (payment #{pay501['id']}, {money(pay501['amount'])})" if pay501 else "")
              + f"; Bulldog Print Co is {'still blocked' if open_bulldog else 'no longer blocked'} (R3); size S on hand: "
              f"{qty[('CC-TEE-WHITE', 'S')]}. Delivery of the reprint is not recorded."),
        102: (f"Records now (2026-10-11): rent payment #{pay_rent['id']} ({money(pay_rent['amount'])}) is recorded"
              + f"; lease #1 next due {db['leases'][1]['next_due']} (the one-month convention, D6)."
              if pay_rent else "Records now (2026-10-11): no rent payment is recorded."),
        103: (f"Records now (2026-10-11): quote #{quote['id']} is {quote['status']} ({money(quote['amount'])}; no revenue, "
              f"nothing sent); size M on hand: {qty[('CC-HOOD-NAVY', 'M')]}; purchases recorded: {len(purchases)}."
              if quote else "Records now (2026-10-11): no quote is recorded."),
    }


def build_desk(model: dict, capture: dict, src: dict) -> tuple[str, dict]:
    raw = TEMPLATE.read_bytes()
    if sha(raw) != TEMPLATE_SHA256:
        raise BuildError("the Problem 6 template does not match its recorded SHA-256")
    s = raw.decode("utf-8")

    def expected_blocks(text):
        out = {}
        for t in TICKETS:
            a = text.index(f'id="panel-{t}"')
            e = text.index('<div class="card expected-block">', a)
            z = text.index('<div class="card actual-block"', e)
            out[t] = sha(text[e:z].encode("utf-8"))
        return out
    before = expected_blocks(s)
    new = s
    for old, rep in ((TITLE_OLD, TITLE_NEW), (H1_OLD, H1_NEW), (LEDE_OLD, LEDE_NEW)):
        if new.count(old) != 1:
            raise BuildError(f"template text not found: {old[:50]}")
        new = new.replace(old, rep)
    notes = now_notes(src)
    if new.count(FACTS_OLD) != 3:
        raise BuildError("expected three 'Verified facts' headings")
    for t in TICKETS:                                    # one per ticket panel, in order, outside the Expected blocks
        a = new.index(f'id="panel-{t}"')
        i = new.index(FACTS_OLD, a)
        if i > new.index('<div class="card expected-block">', a):
            raise BuildError("facts heading is inside an Expected block")
        new = new[:i] + FACTS_NEW.format(now=esc(notes[t])) + new[i + len(FACTS_OLD):]
    for t in TICKETS:
        placeholder = re.search(rf'<div class="actual-empty" id="actual-{t}">.*?</div>', new, re.S)
        if not placeholder:
            raise BuildError(f"Actual placeholder for ticket {t} not found (already generated?)")
        new = new[:placeholder.start()] + actual_html(t, model["tickets"][t], capture) + new[placeholder.end():]
    cash_placeholder = ('<h2>Cash</h2>\n  <div class="card later" data-section="coming-later"><div class="big">Coming later</div>'
                        '<div>This tab will be completed in a later problem.</div></div>')
    if new.count(cash_placeholder) != 1:
        raise BuildError("Cash placeholder not found")
    new = new.replace(cash_placeholder, "<h2>Cash</h2>\n" + cash_html(model["cash"]))
    status_old = "<tr><th>Status</th><td>Open</td></tr>"
    if new.count(status_old) != 3:
        raise BuildError("expected three request-table Status cells")
    new = new.replace(status_old, "<tr><th>Status</th><td>Open when planned (2026-10-10); now <b>resolved</b> "
                                  "(see Actual)</td></tr>")
    meta_old = '<span><span class="badge expected">Expected</span> = planned &nbsp; <span class="badge actual">Actual</span> = filled in after the live runs</span>'
    if new.count(meta_old) != 1:
        raise BuildError("header meta line not found")
    new = new.replace(meta_old, meta_old + "\n    <span><b>Actual and Cash completed:</b> "
                      # the date of the last resolution in the records, so regenerating later never changes it
                      f"{max(t['resolution']['resolved_at'] for t in model['tickets'].values())[:10]}, generated from the "
                      "recorded evidence by "
                      "<code>scripts/build_p9_evidence.py</code> (Problem 9)</span>")
    new = new.replace("  footer { max-width", EXTRA_CSS + "  footer { max-width", 1)
    foot = re.search(r"<footer>\n(.*?)\n</footer>", new, re.S)
    new = new[:foot.start(1)] + ("  The Expected sections are the plan written before any live agent run (Problem 6), kept exactly as "
                                 "written. The Actual sections and the Cash tab were generated after the runs from "
                                 "<code>output/audit_trail.json</code>, <code>data/audit.db</code>, the working database and the "
                                 "run evidence in <code>output/p9_runs/</code> (Problem 9). The Reflection tab (Problem 10) is inserted unchanged "
                                 "from <code>output/p10_reflection.html</code>.") + new[foot.end(1):]
    if REFLECTION.exists():
        if new.count(REFLECTION_PLACEHOLDER) != 1:
            raise BuildError("Reflection placeholder not found")
        fragment = re.sub(r"\A\s*<!--.*?-->\s*", "", REFLECTION.read_text(encoding="utf-8"), flags=re.S).strip()
        new = new.replace(REFLECTION_PLACEHOLDER, "<h2>Reflection</h2>\n" + fragment)
    after = expected_blocks(new)
    if after != before:
        raise BuildError("an Expected block would change; nothing written")
    return new, before


# ---------------------------------------------------------------- resolved_tickets.json

def contributions(run: dict) -> dict:
    out = {}
    for a in run["agents"]:
        calls = [c for c in run["tool_calls"] if c["agent"] == a]
        out[a] = {"delegated_to": [AGENT[d["to"]] for d in run["delegations"] if d["from"] == a],
                  "delegated_by": sorted({AGENT[d["from"]] for d in run["delegations"] if d["to"] == a}),
                  "tool_calls": dict(Counter(c["tool"] for c in calls)),
                  "records_created": [{"tool": c["tool"], **{k: v for k, v in c["links"].items() if k in ("action_id", "draft_id")}}
                                      for c in calls if c["tool"] in WRITE_TOOLS and c["status"] == "ok"],
                  "refused_calls": [{"tool": c["tool"], "code": c["code"]} for c in calls if c["status"] not in ("ok", None)]}
    return out


def outcome_text(t: int, tk: dict, db: dict) -> str:
    res = tk["resolution"]
    pays = ", ".join(f"payment #{p['id']} of {money(p['amount'])} ({p['kind']} {p['ref_id']})" for p in tk["payments"]) or "no payment"
    quotes = [a for a in tk["actions"] if a["kind"] == "quote"]
    q = (f"; quote #{quotes[0]['id']} ({money(quotes[0]['amount'])}) approved internally, no revenue" if quotes else "")
    drafts = ", ".join(f"#{d['id']}" for d in tk["drafts"])
    if res["basis"] == "business_outcome":
        return (f"Business outcome confirmed by {res['resolved_by']}: {pays} executed through the human approval path; "
                f"draft {drafts} unsent.")
    return (f"Simulated workflow complete ({pays}{q}; draft {drafts} unsent). Not verified: "
            + "; ".join(UNVERIFIED[u] for u in res["unverified_outcomes"]) + ".")


def build_json(model: dict, src: dict, capture: dict, expected_hashes: dict) -> dict:
    db = src["db"]
    tickets = []
    for t in TICKETS:
        tk = model["tickets"][t]; res = tk["resolution"]; final = tk["final_run"]
        tickets.append({
            "ticket_id": t,
            "final_status": tk["row"]["status"],
            "outcome": outcome_text(t, tk, db),
            "agent_contributions": {AGENT[a]: v for a, v in contributions(final).items()},
            "agent_contributions_run_id": final["run_id"],
            "human_approvals": [{"by": d["by"], "at_utc": d["at"], "operation": d["operation"], "action_id": d["action_id"],
                                 "result": d["result"], "payment_id": d["payment_id"], "trail_seq": d["trail_seq"],
                                 "resolution_basis_recorded": d["resolution_basis"]}
                                for d in tk["decisions"]],
            "resolution": {"basis": res["basis"],
                           "basis_source": ("recorded resolution_basis field" if res["basis_recorded"] else
                                            "legacy rule: the audit record has no resolution_basis field and "
                                            "business_outcome_confirmed is true (harness §13.10)"),
                           "business_outcome_confirmed": res["business_outcome_confirmed"],
                           "workflow_complete_confirmed": res["workflow_complete_confirmed"],
                           "unverified_outcomes": res["unverified_outcomes"], "resolved_by": res["resolved_by"],
                           "resolved_at_utc": res["resolved_at"], "summary": res["summary"],
                           "audit_db_event_id": res["audit_db_event_id"]},
            "runs": [{"run_id": r["run_id"], "attempt": i, "status": r["run_status"], "started_utc": r["started"],
                      "finished_utc": r["finished"], "trail_events": r["events"], "usage": r["usage"],
                      "evidence_files": r["evidence"]["observer_files"], "evidence_kind": r["evidence"]["observer_files_kind"],
                      "full_agent_replies": r["full_responses_file"]} for i, r in enumerate(tk["runs"], 1)],
            "final_run_id": final["run_id"],
            "payments": [{"payment_id": p["id"], "kind": p["kind"], "ref_id": p["ref_id"], "amount": p["amount"],
                          "approved_by": p["approved_by"]} for p in tk["payments"]],
            "proposals": [{"id": a["id"], "kind": a["kind"], "status": a["status"], "amount": a["amount"],
                           "payment_id": a["payment_id"]} for a in tk["actions"]],
            "drafts": [{"id": d["id"], "recipient": d["recipient"], "status": d["status"], "sent": False} for d in tk["drafts"]],
            "differences_from_expected": tk["differences"],
            "screenshot": next({k: s[k] for k in ("file", "sha256", "captured_utc")} for s in capture["shots"] if s["ticket_id"] == t),
        })
    c = model["cash"]
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "scripts/build_p9_evidence.py (read-only on all sources)",
        "business_date": "2026-08-31",
        "resolution_bases": {
            "business_outcome": "the person confirmed the underlying business issue is resolved",
            "simulated_workflow_complete": ("the person confirmed only that the desk's simulated workflow is complete; "
                                            "the listed outcomes are NOT verified")},
        "tickets": tickets,
        "cash": {"opening_checking": c["opening"], "opening_source": c["opening_source"],
                 "payments": [{k: l[k] for k in ("payment_id", "ticket_id", "kind", "ref_id", "amount", "proposal_id",
                                                 "approved_by", "executed_at", "audit_db_event_ids")} for l in c["lines"]],
                 "quote_3_cash_effect": 0.0, "ending_checking": c["ending"], "cash_accounts_checking": c["stored"],
                 "reconciles": c["ending"] == c["stored"],
                 "note": "Recorded payments in the simulated Campus Customs system; no real bank transfer occurred."},
        "limitations": [
            "Full agent replies exist only for the runs of tickets 102 and 103; ticket 101's runs predate the full-response "
            "recorder, so only 300-character summaries of agent replies are in the trail. Tool results are 300-character "
            "summaries for every run.",
            "Ticket 103's observer files were reconstructed after the run from the contemporaneous trail, response file and "
            "database rows; no observer was running (output/p9_runs/103_attempt1_recovery_record.json).",
            "No record establishes delivery, shipment or receipt of goods, customer acceptance, or that any message was sent.",
            "Each screenshot's Activity panel shows only the first 8-9 events; the complete events are in the trail."],
        "sources": {"audit_trail_events": len(src["trail"]), "audit_db_events": len(db["audit_events"]),
                    "expected_block_sha256": {str(k): v for k, v in expected_hashes.items()}},
    }


# ---------------------------------------------------------------- resolved_board.html

def build_board(model: dict, capture: dict) -> str:
    cards = []
    for t in TICKETS:
        tk = model["tickets"][t]; res = tk["resolution"]
        shot = next(s for s in capture["shots"] if s["ticket_id"] == t)
        data = (HW5 / shot["file"]).read_bytes()
        if sha(data) != shot["sha256"]:
            raise BuildError(f"screenshot for ticket {t} does not match its recorded SHA-256")
        ev = shot["activity_events"]
        simulated = res["basis"] != "business_outcome"
        label = ("RESOLVED · simulated workflow complete; fulfilment not verified" if simulated
                 else "RESOLVED · business outcome confirmed")
        legacy = ("" if res["basis_recorded"] else
                  f'<p class="small">{esc(LEGACY_NOTE.format(event=res["audit_db_event_id"]))}</p>')
        unv = ("<p><b>Not verified in the system's records:</b> " + "; ".join(esc(UNVERIFIED[u]) for u in res["unverified_outcomes"]) + ".</p>") if simulated else ""
        cards.append(f"""<section class="ticket" id="ticket-{t}">
  <h2>Ticket {t}: {esc(tk['row']['subject'])} <span class="label {'sim' if simulated else 'biz'}">{esc(label)}</span></h2>
  <p class="small">Status from the database (<code>tickets.status</code>): <b>{esc(tk['row']['status'])}</b>. Resolution type from
     <code>data/audit.db</code> event {res['audit_db_event_id']}: <code>{esc(res['basis'])}</code>, by {esc(res['resolved_by'])},
     {esc(utc(res['resolved_at']))}.</p>
  {unv}{legacy}
  <p><b>Outcome:</b> {esc(outcome_text(t, tk, None))}</p>
  <figure>
    <img alt="Final-state Campus Customs dashboard, ticket {t} selected" src="data:image/png;base64,{base64.b64encode(data).decode('ascii')}">
    <figcaption>Genuine dashboard screenshot, embedded byte-for-byte: <code>{esc(shot['file'])}</code>, SHA-256
      <code>{esc(shot['sha256'])}</code>, captured {esc(utc(shot['captured_utc']))} from {esc(capture['source'])} (headless
      Edge, read-only). The Activity panel scrolls: it shows {ev['visible_in_scrollable_panel']['any_part']} of the run's
      {ev['complete_count_in_audit_trail']['value']} recorded events; all are in <code>output/audit_trail.json</code>. Times in
      the image are local (UTC−4).</figcaption>
  </figure>
</section>""")
    c = model["cash"]
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Campus Customs: resolved board (Homework 5, Problem 9)</title>
<style>
  body {{ margin: 0; font: 15px/1.55 "Segoe UI", system-ui, sans-serif; color: #1d2433; background: #f5f7fb; }}
  header {{ background: #0f3d6e; color: #fff; padding: 20px 32px; }} header h1 {{ margin: 0 0 4px; font-size: 22px; }}
  header p {{ margin: 0; color: #d5e2f2; font-size: 13.5px; }}
  main {{ max-width: 1180px; margin: 0 auto; padding: 20px 24px 40px; }}
  .card, section.ticket {{ background: #fff; border: 1px solid #d9dee8; border-radius: 10px; padding: 16px 20px; margin: 0 0 18px; }}
  h2 {{ font-size: 18px; color: #0f3d6e; margin: 0 0 8px; }}
  .label {{ display: inline-block; font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 999px; vertical-align: middle; }}
  .label.biz {{ background: #e8f5ee; color: #1f6f43; border: 1px solid #b7dcc6; }}
  .label.sim {{ background: #fff7e6; color: #8a5a00; border: 1px solid #f0d49b; }}
  figure {{ margin: 10px 0 0; }} img {{ max-width: 100%; border: 1px solid #d9dee8; border-radius: 6px; }}
  figcaption, .small {{ font-size: 12.5px; color: #5b6475; }}
  code {{ font-family: Consolas, Menlo, monospace; font-size: 12.5px; background: #f0f2f7; padding: 1px 4px; border-radius: 4px; word-break: break-all; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 14px; }} th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #d9dee8; }}
  blockquote {{ margin: 6px 0; padding: 6px 12px; border-left: 3px solid #b7c3d6; background: #f8fafc; }}
</style>
</head>
<body>
<header>
  <h1>Campus Customs: the three resolved tickets</h1>
  <p>Homework 5, Problem 9. Shop business date 2026-08-31. Generated {esc(datetime.now(timezone.utc).date().isoformat())} by
     <code style="background:none;color:#fff">scripts/build_p9_evidence.py</code> from the recorded evidence; the screenshots are the
     unedited final-state captures.</p>
</header>
<main>
<div class="card">
  <h2>What "resolved" means here</h2>
  <p>Two requirement statements apply. Problem 7: <q>{esc(REQ_P7)}</q> Problem 8 (summary): the dashboard
     <q>{esc(REQ_P8)}</q> Our dashboard does not resolve a ticket automatically. When a run finishes it shows the human steps,
     and a person resolves the ticket by stating exactly what they confirm. There are two resolution types:</p>
  <ul>
    <li><span class="label biz">Business outcome confirmed</span> The person confirmed that the underlying business issue is
        resolved (ticket 102: the rent was paid through the human approval path).</li>
    <li><span class="label sim">Simulated workflow complete; fulfilment not verified</span> The person confirmed only that the
        desk's work in this simulation is complete: agents ran, every proposal was decided, messages were drafted. The listed
        outcomes are not verified (tickets 101 and 103).</li>
  </ul>
  <p>The simulation cannot record physical fulfilment: the shop date never advances (R5) and messages are never sent (R7).
     Nothing on this page claims that goods were delivered or that a customer accepted anything.</p>
</div>
{chr(10).join(cards)}
<div class="card">
  <h2>Cash</h2>
  <table><tr><th>Item</th><th>Change</th><th>Checking</th></tr>
  <tr><td>Opening balance after the controlled reset</td><td>—</td><td>{esc(money(c['opening']))}</td></tr>
  {''.join(f"<tr><td>Payment #{l['payment_id']} (ticket {l['ticket_id']}, {esc(l['kind'])} {l['ref_id']})</td><td>{esc(money(l['amount']))}</td><td></td></tr>" for l in sorted(c['lines'], key=lambda l: l['executed_at']))}
  <tr><td>Quote #{c['quotes'][0]['id']} (ticket {c['quotes'][0]['ticket_id']}): approved internally, no revenue (R6)</td><td>$0.00</td><td></td></tr>
  <tr><td><b>Ending balance</b> (equals <code>cash_accounts</code>)</td><td>{esc(money(c['ending'] - c['opening']))}</td><td><b>{esc(money(c['ending']))}</b></td></tr>
  </table>
  <p class="small">Recorded payments within the simulated system; no real bank transfer occurred. Details: the Cash tab of
     <code>output/desk_tickets.html</code> and <code>output/resolved_tickets.json</code>.</p>
</div>
</main>
</body>
</html>
"""


# ---------------------------------------------------------------- claims check (also used by the tests)

def unsupported_claims(text: str) -> list[str]:
    """Sentences in GENERATED prose that mention delivery, shipment, acceptance or fulfilment without a negation.
    Verbatim records (blockquotes, figure captions of task text) are excluded by the caller."""
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = html.unescape(plain)
    bad = []
    for sentence in re.split(r"(?<=[.!?])\s+", plain):
        if CLAIM.search(sentence) and not NEGATION.search(sentence):
            bad.append(sentence.strip()[:200])
    return bad


def generated_only(fragment: str) -> str:
    """Remove verbatim records (the person's quoted summaries and the agents' recorded tasks) from a fragment."""
    fragment = re.sub(r'<blockquote class="record">.*?</blockquote>', " ", fragment, flags=re.S)
    return re.sub(r"<span class='small'>.*?</span>", " ", fragment, flags=re.S)


def main() -> None:
    prompts = (HW5 / "AI_prompts.md").read_text(encoding="utf-8")
    for q in (REQ_P7, REQ_P8):
        if q not in prompts:
            raise BuildError(f"requirement quote not found verbatim in AI_prompts.md: {q!r}")
    preserved = Path(sys.argv[sys.argv.index("--replace-preserved") + 1]) if "--replace-preserved" in sys.argv else None
    existing = [f for f in (DESK, JSON_OUT, BOARD) if f.exists() and f != DESK or (f == DESK and "actual-done" in f.read_text(encoding="utf-8"))]
    if existing and "--dry-run" not in sys.argv:
        if preserved is None:
            raise BuildError("outputs already exist; refusing to overwrite (use --replace-preserved <folder>)")
        sums = dict(reversed(line.split(" *", 1)) for line in (preserved / "SHA256SUMS.txt").read_text().splitlines() if line.strip())
        for f in existing:
            copy = preserved / f.name
            if not copy.exists() or copy.read_bytes() != f.read_bytes() or sums.get(f.name) != sha(f.read_bytes()):
                raise BuildError(f"{f.name}: no verified preserved copy in {preserved}; refusing to replace")
    src = gather()
    model = build_model(src)
    desk, expected_hashes = build_desk(model, src["capture"], src)
    board = build_board(model, src["capture"])
    data = build_json(model, src, src["capture"], expected_hashes)
    for name, text in (("desk", desk), ("board", board)):
        bad = unsupported_claims(generated_only(text))
        if bad:
            raise BuildError(f"unsupported fulfilment/acceptance wording in {name}: {bad}")
    if "--dry-run" in sys.argv:                      # build and check everything, write to a given folder instead
        target = Path(sys.argv[sys.argv.index("--dry-run") + 1])
        (target / DESK.name).write_text(desk, encoding="utf-8")
        (target / JSON_OUT.name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        (target / BOARD.name).write_text(board, encoding="utf-8")
        print(f"dry run: written to {target}")
        return
    staged = []
    outputs = ((DESK, desk), (JSON_OUT, json.dumps(data, indent=1, ensure_ascii=False) + "\n"), (BOARD, board))
    if "--only-desk" in sys.argv:                          # everything is built and checked; only the desk file is written
        outputs = outputs[:1]
    for f, text in outputs:
        tmp = f.with_name(f.name + ".building")
        tmp.write_text(text, encoding="utf-8")
        if tmp.read_text(encoding="utf-8") != text:
            raise BuildError(f"{tmp.name} did not read back identically; nothing replaced")
        staged.append((tmp, f))
    for tmp, f in staged:                               # all three built and checked: now move them into place
        os.replace(tmp, f)
    print(json.dumps({"written": [str(DESK.relative_to(HW5)), str(JSON_OUT.relative_to(HW5)), str(BOARD.relative_to(HW5))],
                      "expected_block_sha256": expected_hashes,
                      "cash": [model["cash"]["opening"], [l["amount"] for l in model["cash"]["lines"]], model["cash"]["ending"]],
                      "sha256": {f.name: sha(f.read_bytes()) for f in (DESK, JSON_OUT, BOARD)}}, indent=1))


if __name__ == "__main__":
    main()
