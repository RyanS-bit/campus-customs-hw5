"""Write output/mcp_regression_p5.json: offline MCP evidence for Problem 5, Stage 3.

Uses FastMCP's in-memory client against servers built by server.create_server(), on a fresh
TEMPORARY copy of the database (never data/). These are NOT live vibe-coder tool calls; the
Problem 4 live evidence in output/mcp_smoke.json is read for comparison and never modified.

Run from hw5/:  .venv/Scripts/python scripts/stage3_evidence.py
"""

import json
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

HW5 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HW5 / "mcp_server"))
sys.path.insert(0, str(HW5 / "tests"))
import maintenance  # noqa: E402
import ops  # noqa: E402
import paths  # noqa: E402
import server  # noqa: E402
from mcp_helpers import call, text, tool_names  # noqa: E402


def outcome(r):
    return {"is_error": r.is_error, "error_text": text(r)} if r.is_error else {"is_error": False, "result": r.structured_content}


def main() -> int:
    smoke_path = HW5 / "output" / "mcp_smoke.json"
    smoke_before = paths.sha256_file(smoke_path)
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    real = {n: paths.sha256_file(HW5 / "data" / n) for n in (paths.ORIGINAL_NAME, paths.WORKING_NAME)}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "hw5"
        (root / "data").mkdir(parents=True)
        (root / "output").mkdir()
        shutil.copyfile(HW5 / "data" / paths.ORIGINAL_NAME, root / "data" / paths.ORIGINAL_NAME)
        p = paths.resolve(env={}, root=root, use_dotenv=False)
        maintenance.reset_working_db(p)
        run = str(uuid.uuid4())
        dev = server.create_server(None, p)
        agent = {a: server.create_server(ops.RunIdentity(run, a, "simulated"), p) for a in ops.AGENTS}

        compat = []
        for c in smoke["calls"] + smoke["additional_verification_calls"]["calls"]:
            r = call(dev, c["tool"], c["input"])
            expected = {"is_error": True, "error_text": c["error_text"]} if "error_text" in c else {"is_error": False, "result": c["output"]}
            compat.append({"problem4_call": c["n"], "tool": c["tool"], "input": c["input"], "identical": outcome(r) == expected})

        reads = {t: outcome(call(dev, t, a)) for t, a in
                 [("get_ticket", {"ticket_id": 101}), ("list_vendors", {}), ("list_pending_actions", {})]}

        authorized = [
            ("accounting", "propose_payment", dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")),
            ("accounting", "propose_payment", dict(kind="lease", ref_id=1, amount=2400, ticket_id=102, rationale="Rent due 2026-09-02")),
            ("inventory", "propose_purchase", dict(vendor_id=1, sku="CC-HOOD-NAVY", size="M", qty=12, ticket_id=103, rationale="Cover the 12-unit shortfall")),
            ("customer_service", "propose_quote", dict(ticket_id=103, sku="CC-HOOD-NAVY", size="M", qty=20, unit_price=45, rationale="Bulk discount within policy")),
            ("customer_service", "draft_message", dict(ticket_id=103, subject="Hoodie order", body="We can offer $45.00 per hoodie; availability is being confirmed.", related_action_id=4)),
            ("facilities", "draft_message", dict(ticket_id=102, subject="Rent notice", body="Your rent notice was received and is under review.")),
        ]
        authorized_results = [{"agent": a, "tool": t, "args": args, **outcome(call(agent[a], t, args))} for a, t, args in authorized]

        forged_run = str(uuid.uuid4())
        denied = [
            ("development mode (no launch identity)", dev, "propose_payment", authorized[0][2], None),
            ("development mode with forged meta", dev, "propose_payment", authorized[0][2], {"agent": "accounting", "run_id": forged_run}),
            ("customer_service calls accounting's tool", agent["customer_service"], "propose_payment", authorized[0][2], None),
            ("boss (read-only role) calls a write tool", agent["boss"], "propose_quote", authorized[3][2], None),
            ("forged argument 'agent'", agent["customer_service"], "propose_quote", {**authorized[3][2], "unit_price": 46, "agent": "accounting"}, None),
            ("customer_service drafts to the landlord", agent["customer_service"], "draft_message", authorized[5][2], None),
            ("facilities drafts to a customer", agent["facilities"], "draft_message", dict(ticket_id=101, subject="Tee", body="Customer message from facilities."), None),
            ("duplicate payment proposal", agent["accounting"], "propose_payment", authorized[0][2], None),
            ("quote below unit cost", agent["customer_service"], "propose_quote", {**authorized[3][2], "unit_price": 21}, None),
        ]
        denied_results = [{"scenario": s, "tool": t, **outcome(call(srv, t, args, meta=meta))} for s, srv, t, args, meta in denied]

        forged_meta = call(agent["customer_service"], "propose_quote", {**authorized[3][2], "unit_price": 50},
                           meta={"agent": "accounting", "run_id": forged_run, "call_id": "evidence-c3"})
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(p.working_db)) as con:
            attribution = con.execute("SELECT proposed_by, run_id = ?, run_id = ?, call_id FROM pending_actions WHERE unit_price = 50",
                                      (run, forged_run)).fetchone()

        evidence = {
            "label": "OFFLINE in-memory MCP client on a temporary database copy. NOT live vibe-coder calls and NOT agent runs.",
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "backward_compatibility_vs_mcp_smoke_json": compat,
            "development_mode_tools": sorted(tool_names(dev)),
            "agent_tool_registration": {a: sorted(tool_names(s)) for a, s in agent.items()},
            "new_read_tools_on_fresh_reset": reads,
            "authorized_proposals_and_drafts": authorized_results,
            "denied_operations": denied_results,
            "forged_meta_attribution": {"proposed_by": attribution[0], "run_id_is_launch_run": bool(attribution[1]),
                                        "run_id_is_forged_run": bool(attribution[2]), "call_id_kept_for_correlation": attribution[3]},
            "final_counts": {"pending_actions": len(call(dev, "list_pending_actions").structured_content["actions"])},
        }

    evidence["mcp_smoke_json_unchanged"] = paths.sha256_file(smoke_path) == smoke_before
    evidence["real_databases_unchanged"] = all(paths.sha256_file(HW5 / "data" / n) == h for n, h in real.items())
    out = HW5 / "output" / "mcp_regression_p5.json"
    out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    ok = (all(c["identical"] for c in evidence["backward_compatibility_vs_mcp_smoke_json"])
          and not any(r["is_error"] for r in evidence["authorized_proposals_and_drafts"])
          and all(r["is_error"] for r in evidence["denied_operations"])
          and evidence["forged_meta_attribution"]["proposed_by"] == "customer_service"
          and evidence["mcp_smoke_json_unchanged"] and evidence["real_databases_unchanged"])
    print(f"wrote {out.name}: compatible={all(c['identical'] for c in compat)}, "
          f"authorized ok={sum(not r['is_error'] for r in authorized_results)}/{len(authorized_results)}, "
          f"denied={sum(r['is_error'] for r in denied_results)}/{len(denied_results)}, all checks ok={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
