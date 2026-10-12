"""Shared fixtures. Every test works in an isolated temporary copy of the hw5 layout.

The session guard fails the run if any real database file in hw5/data changes.
"""

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

REAL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REAL_ROOT / "mcp_server"))
sys.path.insert(0, str(REAL_ROOT / "backend"))
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import paths  # noqa: E402

REAL_ORIGINAL = REAL_ROOT / "data" / paths.ORIGINAL_NAME
REAL_FILES = [REAL_ROOT / "data" / n for n in (paths.ORIGINAL_NAME, paths.WORKING_NAME, paths.AUDIT_DB_NAME)] + [
    REAL_ROOT / "output" / paths.AUDIT_TRAIL_NAME]                       # the real audit trail must not change either


def _fingerprint(files):
    return {str(f): (paths.sha256_file(f), f.stat().st_mtime_ns) if f.exists() else None for f in files}


@pytest.fixture(scope="session", autouse=True)
def real_project_files_untouched():
    before = _fingerprint(REAL_FILES)
    yield
    assert _fingerprint(REAL_FILES) == before, "A real hw5/data database file changed during the tests"


@pytest.fixture
def sandbox(tmp_path) -> Path:
    """A throwaway hw5 root: data/ (with a read-only copy of the original) and output/."""
    root = tmp_path / "hw5"
    (root / "data").mkdir(parents=True)
    (root / "output").mkdir()
    original = root / "data" / paths.ORIGINAL_NAME
    shutil.copyfile(REAL_ORIGINAL, original)
    os.chmod(original, stat.S_IREAD)
    return root


@pytest.fixture
def runtime_dir(tmp_path) -> Path:
    return tmp_path / "runtime"


def make_writable(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


# ---- Stage 2 helpers ---------------------------------------------------------------

PROTECTED_TABLES = {"desk", "cash_accounts", "inventory", "pricing", "vendors", "invoices", "leases",
                    "payments", "tickets", "schema_ext_meta"}


@pytest.fixture
def work(sandbox):
    """Paths for a freshly reset sandbox working copy (default layout)."""
    import maintenance
    p = paths.resolve(env={}, root=sandbox, use_dotenv=False)
    maintenance.reset_working_db(p)
    return p


@pytest.fixture
def ident():
    import uuid

    import ops
    run_id = str(uuid.uuid4())
    return lambda agent, mode="simulated": ops.RunIdentity(run_id, agent, mode)


def privileged(p):
    """Unrestricted connection to the working copy with audit.db attached (test setup only)."""
    import maintenance
    import audit_db
    from contextlib import closing
    audit_db.ensure(p.audit_db)
    con = maintenance.connect_rw(p.working_db)
    con.execute("ATTACH DATABASE ? AS audit", (str(p.audit_db),))
    return closing(con)


def counts(p) -> dict:
    import maintenance
    import audit_db
    from contextlib import closing
    audit_db.ensure(p.audit_db)
    with closing(maintenance.connect_ro(p.working_db)) as w, closing(maintenance.connect_ro(p.audit_db)) as a:
        return {"pending_actions": w.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0],
                "drafts": w.execute("SELECT COUNT(*) FROM drafts").fetchone()[0],
                "events": a.execute("SELECT COUNT(*) FROM events").fetchone()[0]}


def protected_digest(p) -> str:
    import maintenance
    from contextlib import closing
    with closing(maintenance.connect_ro(p.working_db)) as w:
        return maintenance.content_digest(w, only=PROTECTED_TABLES)
