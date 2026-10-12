"""Verify and reconcile the audit records (Problem 5, Stage 4). A human operator's tool, never an MCP tool.

Run from hw5/:
  .venv/Scripts/python scripts/verify_audit.py                       report only (default; writes nothing)
  .venv/Scripts/python scripts/verify_audit.py reconcile --confirm   append reconciliation events for incomplete calls
  .venv/Scripts/python scripts/verify_audit.py init --confirm        create output/audit_trail.json if it does not exist

Checks: the JSON hash chain, the audit.db hash chain, and that every audit.db event from a run
matches a JSON tool call (and vice versa). Exit 0 = consistent, 1 = problems found.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_server"))
import audit_reconcile  # noqa: E402
import audit_trail  # noqa: E402
import paths  # noqa: E402


def resolve_for(root):
    """With --root (offline tests), ignore CC_RUNTIME_DIR from the process and hw5/.env entirely,
    so a stray setting can never redirect the operation to a real runtime folder (remediation m1)."""
    if root is not None:
        return paths.resolve(env={}, root=root, use_dotenv=False)
    return paths.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", nargs="?", default="check", choices=["check", "reconcile", "init"])
    parser.add_argument("--confirm", action="store_true", help="required for actions that append events")
    parser.add_argument("--root", type=Path, help="hw5 root to operate on (used by the offline tests)")
    args = parser.parse_args(argv)
    p = resolve_for(args.root)
    trail = audit_trail.AuditTrail(p.audit_trail_json)

    if args.action in ("reconcile", "init") and not args.confirm:
        print(f"Nothing changed. Re-run '{args.action}' with --confirm.", file=sys.stderr)
        return 2
    try:
        if args.action == "init":
            if trail.path.exists():
                print(f"{trail.path.name} already exists; nothing to initialize.")
            else:
                trail.append([audit_trail.system_event("trail_initialized", outcome={"status": "ok"},
                                                       details={"note": "Audit trail created. Earlier activity "
                                                                "(Problems 1-4, Stage 1-3 tests, the Stage 3 reset) "
                                                                "is documented in AI_prompts.md and output/, not here."})])
                print(f"Created {trail.path}")
        elif args.action == "reconcile":
            added = audit_reconcile.reconcile(p, trail)
            print(f"Appended {len(added)} reconciliation event(s).")
    except audit_reconcile.ReconcileRefused as exc:        # remediation M3
        print(f"RECONCILE REFUSED: {exc}", file=sys.stderr)
        return 1
    except audit_trail.AuditTrailError as exc:
        print(f"AUDIT TRAIL ERROR: {exc}", file=sys.stderr)
        return 1
    report = audit_reconcile.check(p, trail)
    print(json.dumps(report, indent=2))
    return 0 if report["consistent"] else 1


if __name__ == "__main__":
    sys.exit(main())
