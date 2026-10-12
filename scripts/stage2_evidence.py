"""Write output/p5_stage2_authz_evidence.json: authorization and rollback evidence (Problem 5, Stage 2).

Runs entirely in a fresh TEMPORARY copy of the hw5 layout (never data/), through the real
ops.open_write_connection and ops functions. Offline: no model or network calls.

Run from hw5/:  .venv/Scripts/python scripts/stage2_evidence.py
"""

import json
import shutil
import sqlite3
import sys
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

HW5 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HW5 / "mcp_server"))
import audit_db  # noqa: E402
import maintenance  # noqa: E402
import ops  # noqa: E402
import paths  # noqa: E402

PROTECTED = {"desk", "cash_accounts", "inventory", "pricing", "vendors", "invoices", "leases", "payments", "tickets", "schema_ext_meta"}


def snapshot(p):
    with closing(maintenance.connect_ro(p.working_db)) as w, closing(maintenance.connect_ro(p.audit_db)) as a:
        return {"protected_tables_digest": maintenance.content_digest(w, only=PROTECTED),
                "cash_balance": w.execute("SELECT balance FROM cash_accounts").fetchone()[0],
                "pending_actions": w.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0],
                "drafts": w.execute("SELECT COUNT(*) FROM drafts").fetchone()[0],
                "audit_events": a.execute("SELECT COUNT(*) FROM events").fetchone()[0]}


def main() -> int:
    original_hashes = {n: paths.sha256_file(HW5 / "data" / n) for n in (paths.ORIGINAL_NAME, paths.WORKING_NAME)}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "hw5"
        (root / "data").mkdir(parents=True)
        (root / "output").mkdir()
        shutil.copyfile(HW5 / "data" / paths.ORIGINAL_NAME, root / "data" / paths.ORIGINAL_NAME)
        p = paths.resolve(env={}, root=root, use_dotenv=False)
        maintenance.reset_working_db(p)
        run = str(uuid.uuid4())
        who = {a: ops.RunIdentity(run, a, "simulated") for a in ops.AGENTS}
        audit_db.ensure(p.audit_db)

        legit = ops.propose_payment(p, who["accounting"], kind="invoice", ref_id=501, amount=840, ticket_id=101,
                                    rationale="Pay overdue invoice 501")
        evidence = {"label": "OFFLINE evidence on a temporary copy of the database. Not live agent activity.",
                    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "legitimate_proposal": {"links": legit["links"], "state_after": snapshot(p)},
                    "denied_direct_attempts": [], "denied_indirect_attempts": [], "rollback": []}

        attempts = [
            ("direct", "UPDATE cash_accounts SET balance = 0"),
            ("direct", "UPDATE invoices SET status = 'paid' WHERE id = 501"),
            ("direct", "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES ('invoice', 501, 840, 'checking', 'x', 'agent')"),
            ("direct", "UPDATE tickets SET status = 'closed'"),
            ("direct", "UPDATE inventory SET qty = qty + 100"),
            ("direct", "UPDATE leases SET next_due = '2026-10-02'"),
            ("direct", "UPDATE pending_actions SET status = 'approved'"),
            ("direct", "DELETE FROM audit.events"),
            ("indirect", "CREATE TEMP TRIGGER t AFTER INSERT ON pending_actions BEGIN UPDATE cash_accounts SET balance = 0; END"),
            ("indirect", "DROP TRIGGER pending_actions_insert_as_proposed"),
            ("indirect", f"ATTACH DATABASE '{p.original_db}' AS orig"),
            ("indirect", "PRAGMA foreign_keys = OFF"),
            ("indirect", "PRAGMA recursive_triggers = OFF"),
            ("indirect", f"VACUUM INTO '{Path(tmp) / 'copy.db'}'"),
            ("indirect", "INSERT OR REPLACE INTO pending_actions (id, kind, invoice_id, amount, amount_basis, account, "
                         "fundable_at_proposal, rationale, proposed_by, run_id, business_date, created_at) VALUES "
                         "(1, 'payment', 501, 840, 'invoice_amount', 'checking', 1, 'forged replacement', 'accounting', 'r', 'd', 't')"),
        ]
        for kind, sql in attempts:
            before = snapshot(p)
            with closing(ops.open_write_connection(p)) as con:
                try:
                    con.execute(sql)
                    outcome = "ALLOWED"
                except sqlite3.DatabaseError as exc:
                    outcome = f"refused: {exc}"
                if con.in_transaction:
                    con.execute("ROLLBACK")
                denials = list(con.denials)
            evidence[f"denied_{kind}_attempts"].append(
                {"sql": sql, "outcome": outcome, "authorizer_denials": denials, "state_unchanged": snapshot(p) == before})

        # Hostile trigger planted by privileged setup code, then fired by a legitimate operation.
        with closing(maintenance.connect_rw(p.working_db)) as con:
            con.execute("CREATE TRIGGER hostile AFTER INSERT ON pending_actions BEGIN UPDATE cash_accounts SET balance = 0; END")
        before = snapshot(p)
        try:
            ops.propose_payment(p, who["accounting"], kind="lease", ref_id=1, amount=2400, rationale="Rent due 2026-09-02")
            outcome = "ALLOWED"
        except ops.OpsError as exc:
            outcome = str(exc)
        evidence["denied_indirect_attempts"].append({"scenario": "hostile trigger fired by a real propose_payment",
                                                     "outcome": outcome, "state_unchanged": snapshot(p) == before})
        with closing(maintenance.connect_rw(p.working_db)) as con:
            con.execute("DROP TRIGGER hostile")

        # Rollback: failure injected between the record and its audit event, and after both.
        for stage in ("after_row", "after_audit"):
            before = snapshot(p)
            ops._fault_hook = lambda s, stage=stage: (_ for _ in ()).throw(RuntimeError(f"injected at {s}")) if s == stage else None
            try:
                ops.draft_message(p, who["customer_service"], ticket_id=101, subject="Tee", body="Thanks for your order request.")
                outcome = "ALLOWED"
            except RuntimeError as exc:
                outcome = f"failed as injected: {exc}"
            finally:
                ops._fault_hook = None
            evidence["rollback"].append({"scenario": f"failure injected {stage}", "outcome": outcome,
                                         "state_unchanged": snapshot(p) == before})

        with closing(sqlite3.connect(p.audit_db, isolation_level=None)) as con:
            con.execute("CREATE TRIGGER simulated_failure BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END")
        before = snapshot(p)
        try:
            ops.propose_quote(p, who["customer_service"], ticket_id=103, sku="CC-HOOD-NAVY", size="M", qty=20,
                              unit_price=45, rationale="Bulk discount within policy")
            outcome = "ALLOWED"
        except ops.OpsError as exc:
            outcome = str(exc)
        evidence["rollback"].append({"scenario": "audit.db write fails", "outcome": outcome,
                                     "state_unchanged": snapshot(p) == before})

        evidence["final_state"] = snapshot(p)
        evidence["audit_chain"] = audit_db.verify_chain(p.audit_db)

    evidence["real_databases_unchanged"] = all(
        paths.sha256_file(HW5 / "data" / n) == h for n, h in original_hashes.items())
    out = HW5 / "output" / "p5_stage2_authz_evidence.json"
    out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    allowed = [a for k in ("denied_direct_attempts", "denied_indirect_attempts") for a in evidence[k] if a["outcome"] == "ALLOWED"]
    unchanged = all(a["state_unchanged"] for k in ("denied_direct_attempts", "denied_indirect_attempts", "rollback") for a in evidence[k])
    print(f"wrote {out.name}: {len(allowed)} attempts allowed, all states unchanged = {unchanged}, "
          f"real databases unchanged = {evidence['real_databases_unchanged']}")
    return 0 if not allowed and unchanged and evidence["real_databases_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
