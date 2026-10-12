"""Single source of truth for Campus Customs file locations (Problem 5, Stage 1).

Default (no configuration): everything lives where the assignment puts it.
    data/campus_customs.db       original, read-only, never written
    data/campus_customs_new.db   working copy
    data/audit.db                supplementary financial audit database
    output/audit_trail.json      required agent audit trail (always in the repository)

Optional: set CC_RUNTIME_DIR (environment or hw5/.env) to an absolute path to run the
working copy and audit.db from a local, unsynchronised folder. data/campus_customs_new.db
then becomes a snapshot target (see maintenance.snapshot_runtime).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

HW5_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ENV_VAR = "CC_RUNTIME_DIR"

ORIGINAL_NAME = "campus_customs.db"
WORKING_NAME = "campus_customs_new.db"
AUDIT_DB_NAME = "audit.db"
AUDIT_TRAIL_NAME = "audit_trail.json"

# SHA-256 of the supplied original database, recorded in output/harness.md section 1.
ORIGINAL_SHA256 = "23686a90d698f7fa6f901a3f0a9774db9cbb31e13151c9e5717275f3321f360a"


class PathConfigError(ValueError):
    """CC_RUNTIME_DIR is set but unusable."""


class UnsafeWriteError(PermissionError):
    """A write was attempted against the original database."""


@dataclass(frozen=True)
class Paths:
    root: Path
    mode: str                 # "default" (data/) or "runtime" (CC_RUNTIME_DIR)
    original_db: Path
    working_db: Path          # the ACTIVE working copy
    audit_db: Path
    audit_trail_json: Path
    repo_working_db: Path     # data/campus_customs_new.db (snapshot target in runtime mode)

    @property
    def active_dir(self) -> Path:
        return self.working_db.parent

    def describe(self) -> dict:
        return {
            "mode": self.mode,
            "original_db": str(self.original_db),
            "working_db": str(self.working_db),
            "audit_db": str(self.audit_db),
            "audit_trail_json": str(self.audit_trail_json),
            "repo_working_db": str(self.repo_working_db),
        }


def _runtime_setting(env: Mapping[str, str], root: Path, use_dotenv: bool) -> str:
    """Process environment first, then hw5/.env. Only CC_RUNTIME_DIR is read."""
    value = env.get(RUNTIME_ENV_VAR)
    if value is None and use_dotenv:
        env_file = root / ".env"
        if env_file.is_file():
            value = dotenv_values(env_file).get(RUNTIME_ENV_VAR)
    return (value or "").strip()


def resolve(env: Mapping[str, str] | None = None, root: Path | None = None, use_dotenv: bool = True) -> Paths:
    """Work out every path. All components (server, backend, scripts, tests) must call this."""
    root = (root or HW5_ROOT).resolve()
    env = os.environ if env is None else env
    data_dir = root / "data"

    setting = _runtime_setting(env, root, use_dotenv)
    active_dir, mode = data_dir, "default"
    if setting:
        expanded = Path(os.path.expandvars(os.path.expanduser(setting)))
        if not expanded.is_absolute():
            raise PathConfigError(f"{RUNTIME_ENV_VAR} must be an absolute path, got {setting!r}")
        if "%" in str(expanded) or "$" in str(expanded):
            raise PathConfigError(f"{RUNTIME_ENV_VAR} contains an unexpanded variable: {setting!r}")
        expanded = expanded.resolve()
        if expanded != data_dir:
            active_dir, mode = expanded, "runtime"

    return Paths(
        root=root,
        mode=mode,
        original_db=data_dir / ORIGINAL_NAME,
        working_db=active_dir / WORKING_NAME,
        audit_db=active_dir / AUDIT_DB_NAME,
        audit_trail_json=root / "output" / AUDIT_TRAIL_NAME,
        repo_working_db=data_dir / WORKING_NAME,
    )


def _same_file(a: Path, b: Path) -> bool:
    if a.exists() and b.exists():
        return os.path.samefile(a, b)
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def assert_writable(target: Path, paths: Paths) -> None:
    """Refuse any write whose target is (or aliases) the original database."""
    if _same_file(Path(target), paths.original_db):
        raise UnsafeWriteError(f"Refusing to write to the original database: {target}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
