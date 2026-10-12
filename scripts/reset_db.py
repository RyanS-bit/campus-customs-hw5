"""Reset the ACTIVE working database from the original (rule R8), then apply schema_ext.sql.

Run from hw5/:  .venv/Scripts/python scripts/reset_db.py --confirm

Uses data/ by default, or CC_RUNTIME_DIR if set. Never writes to data/campus_customs.db or
audit.db. The outcome (success or failure) is APPENDED to output/audit_trail.json as a
'db_reset' system event; the trail's history is never erased. Without --confirm it only
shows what it would do and records nothing.

Exit codes: 0 reset done and recorded; 1 reset failed (failure recorded if possible);
2 dry run; 3 reset DONE but its audit event could NOT be recorded (report this).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_server"))
import audit_trail  # noqa: E402
import maintenance  # noqa: E402
import paths  # noqa: E402


def resolve_for(root):
    """With --root (offline tests), ignore CC_RUNTIME_DIR from the process and hw5/.env entirely,
    so a stray setting can never redirect the operation to a real runtime folder (remediation m1)."""
    if root is not None:
        return paths.resolve(env={}, root=root, use_dotenv=False)
    return paths.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confirm", action="store_true", help="actually replace the working copy")
    parser.add_argument("--root", type=Path, help="hw5 root to operate on (used by the offline tests)")
    args = parser.parse_args(argv)

    try:
        p = resolve_for(args.root)
    except paths.PathConfigError as exc:                   # remediation m4: a clean error, not a traceback
        print(f"RESET FAILED: {exc}", file=sys.stderr)
        return 1
    if not args.confirm:
        print(json.dumps({"dry_run": True, "would_reset": p.describe()}, indent=2))
        print("Nothing changed. Re-run with --confirm to reset.", file=sys.stderr)
        return 2
    trail = audit_trail.AuditTrail(p.audit_trail_json)
    try:
        result = maintenance.reset_working_db(p)
    except (maintenance.ResetError, paths.UnsafeWriteError, paths.PathConfigError) as exc:
        print(f"RESET FAILED: {exc}", file=sys.stderr)
        try:
            trail.append([audit_trail.system_event("db_reset", outcome={"status": "error", "message": str(exc)},
                                                   details={"mode": p.mode, "working_db": str(p.working_db)})])
        except audit_trail.AuditTrailError as audit_exc:
            print(f"(the failure could not be recorded in the audit trail: {audit_exc})", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    try:
        trail.append([audit_trail.system_event("db_reset", outcome={"status": "ok"}, details=result)])
    except audit_trail.AuditTrailError as exc:
        print(f"RESET COMPLETED, BUT ITS AUDIT EVENT WAS NOT RECORDED: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
