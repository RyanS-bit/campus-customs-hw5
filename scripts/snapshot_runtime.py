"""Copy the runtime working database (CC_RUNTIME_DIR) into data/campus_customs_new.db.

Run from hw5/:  .venv/Scripts/python scripts/snapshot_runtime.py

Only meaningful when CC_RUNTIME_DIR is set; in the default layout the working copy already
is data/campus_customs_new.db and the script refuses. Uses SQLite's online backup, so only
committed data is copied, and verifies the copy before replacing the target. Refuses while
an agent run holds the run lock. The outcome is APPENDED to output/audit_trail.json as a
'db_snapshot' system event.

Exit codes: 0 done and recorded; 1 failed (failure recorded if possible);
3 snapshot DONE but its audit event could NOT be recorded (report this).
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
    parser.add_argument("--root", type=Path, help="hw5 root to operate on (used by the offline tests)")
    args = parser.parse_args(argv)
    p = resolve_for(args.root)
    trail = audit_trail.AuditTrail(p.audit_trail_json)
    try:
        result = maintenance.snapshot_runtime(p)
    except (maintenance.SnapshotError, paths.UnsafeWriteError, paths.PathConfigError) as exc:
        print(f"SNAPSHOT FAILED: {exc}", file=sys.stderr)
        try:
            trail.append([audit_trail.system_event("db_snapshot", outcome={"status": "error", "message": str(exc)},
                                                   details={"mode": p.mode})])
        except audit_trail.AuditTrailError as audit_exc:
            print(f"(the failure could not be recorded in the audit trail: {audit_exc})", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    try:
        trail.append([audit_trail.system_event("db_snapshot", outcome={"status": "ok"}, details=result)])
    except audit_trail.AuditTrailError as exc:
        print(f"SNAPSHOT COMPLETED, BUT ITS AUDIT EVENT WAS NOT RECORDED: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
