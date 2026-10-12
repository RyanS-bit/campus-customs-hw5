"""Database reset (rule R8) and runtime snapshot (Problem 5, Stage 1).

reset_working_db   copy the original over the ACTIVE working copy, verify, apply schema_ext.sql
snapshot_runtime   copy the runtime working copy back into data/campus_customs_new.db

Neither function ever opens the original for writing, and neither touches audit.db or
output/audit_trail.json. Both return a plain dict suitable for a system audit event
(recorded from Stage 2/4 onward, decision D17).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import locks
from paths import ORIGINAL_SHA256, Paths, assert_writable, sha256_file

SCHEMA_EXT = Path(__file__).resolve().parent / "schema_ext.sql"
EXTENSION_TABLES = ("schema_ext_meta", "pending_actions", "drafts")
JOURNAL_SUFFIXES = ("-journal", "-wal", "-shm")


class ResetError(RuntimeError):
    pass


class SnapshotError(RuntimeError):
    pass


def connect_rw(path: Path) -> sqlite3.Connection:
    """Read-write connection with the settings our atomicity guarantees depend on (architecture 18.2)."""
    con = sqlite3.connect(path, isolation_level=None, timeout=5.0)
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("PRAGMA synchronous = FULL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    settings = {
        "journal_mode": con.execute("PRAGMA journal_mode").fetchone()[0],
        "synchronous": con.execute("PRAGMA synchronous").fetchone()[0],
        "foreign_keys": con.execute("PRAGMA foreign_keys").fetchone()[0],
    }
    if settings != {"journal_mode": "delete", "synchronous": 2, "foreign_keys": 1}:
        con.close()
        raise sqlite3.OperationalError(f"Required SQLite settings not in effect: {settings}")
    return con


def connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def schema_objects(con: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return con.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()


def content_digest(con: sqlite3.Connection, only: set[str] | None = None) -> str:
    """SHA-256 over schema SQL and every row of every table (optionally limited to `only` names).

    Compares database *content*, independent of page layout, so a snapshot or an extended
    working copy can be checked against its source.
    """
    h = hashlib.sha256()
    for kind, name, sql in schema_objects(con):
        if only is not None and name not in only:
            continue
        h.update(json.dumps([kind, name, sql]).encode())
        if kind == "table":
            for row in con.execute(f'SELECT * FROM "{name}" ORDER BY rowid'):
                h.update(json.dumps(row, default=repr).encode())
    return h.hexdigest()


def integrity_ok(con: sqlite3.Connection) -> bool:
    return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def _ensure_not_in_use(db: Path, error: type[RuntimeError]) -> None:
    """Take and release an exclusive lock. Opening also lets SQLite roll back any hot journal
    on the OLD file first, so the journal removal below can never discard a live transaction."""
    if not db.exists():
        return
    try:
        con = sqlite3.connect(db, isolation_level=None, timeout=0.5)
        try:
            con.execute("BEGIN EXCLUSIVE")
            con.execute("ROLLBACK")
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc) or "busy" in str(exc):
            raise error(f"{db.name} is in use by another connection; close it and retry.") from exc
    except sqlite3.DatabaseError:
        pass  # not a usable database file; it is about to be replaced


def _remove_stale_journals(db: Path) -> list[str]:
    """A leftover journal from an earlier file would be replayed into a new file; remove it first."""
    removed = []
    for suffix in JOURNAL_SUFFIXES:
        j = db.with_name(db.name + suffix)
        if j.exists():
            j.unlink()
            removed.append(j.name)
    return removed


def apply_schema_ext(db: Path) -> None:
    with closing(connect_rw(db)) as con:
        con.executescript(SCHEMA_EXT.read_text(encoding="utf-8"))


def _hold_run_lock(paths: Paths, error: type[RuntimeError]):
    """Hold the run lock for a whole reset or snapshot (remediation m9): refuses if a run is active,
    and no run can start until we finish (RunRecorder takes the same lock without waiting)."""
    lock = locks.file_lock(locks.run_lock_path(paths.active_dir), timeout=0)
    try:
        lock.__enter__()
    except locks.LockTimeout as exc:
        raise error("An agent run is active (run lock held); refusing to proceed.") from exc
    return lock


RESET_STAMP_TABLE = "reset_stamp"


def read_reset_stamp(db: Path) -> str | None:
    """The reset_id stamped into the working copy by the reset that produced it (Problem 7), or None if that
    copy carries no stamp (e.g. a reset made with scripts/reset_db.py). Raises sqlite3.Error if unreadable."""
    with closing(connect_ro(db)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (RESET_STAMP_TABLE,)).fetchone():
            return None
        row = con.execute(f"SELECT reset_id FROM {RESET_STAMP_TABLE}").fetchone()
        return row[0] if row else None


def reset_working_db(paths: Paths, stamp: dict | None = None) -> dict:
    """R8: copy the original, extend and verify the COPY, then swap it in as the working database.
    The working copy is replaced only by a fully prepared and verified file (remediation m9)."""
    assert_writable(paths.working_db, paths)
    lock = _hold_run_lock(paths, ResetError)
    try:
        return reset_working_db_locked(paths, stamp)
    finally:
        lock.__exit__(None, None, None)


def reset_working_db_locked(paths: Paths, stamp: dict | None = None) -> dict:
    """The reset itself, for a caller that ALREADY holds the run lock (approval.controlled_reset).
    stamp = {"reset_id", "requested_by", "requested_at"}: written into the prepared copy BEFORE the atomic swap,
    so the working copy itself proves which reset produced it (used by reconciliation, Problem 7 A3)."""
    original, working = paths.original_db, paths.working_db
    assert_writable(working, paths)
    paths.active_dir.mkdir(parents=True, exist_ok=True)
    staging = working.with_name(working.name + ".resetting")
    try:
        original_before = sha256_file(original)
        if original_before != ORIGINAL_SHA256:
            raise ResetError(f"Original database hash {original_before} does not match the recorded value "
                             f"{ORIGINAL_SHA256}; refusing to reset.")
        staging.unlink(missing_ok=True)
        _remove_stale_journals(staging)
        shutil.copyfile(original, staging)          # copies bytes only; the copy is writable
        copied = sha256_file(staging)
        if copied != ORIGINAL_SHA256:
            raise ResetError("Copy of the original does not match its hash; working copy left unchanged.")

        try:
            apply_schema_ext(staging)               # extend the COPY; the live working copy is untouched so far
        except sqlite3.Error as exc:
            raise ResetError(f"Schema extension failed ({exc}); working copy left unchanged.") from exc
        if stamp is not None:
            try:
                with closing(connect_rw(staging)) as con:
                    con.execute(f"CREATE TABLE {RESET_STAMP_TABLE} (reset_id TEXT NOT NULL, requested_by TEXT NOT NULL, "
                                "requested_at TEXT NOT NULL)")
                    con.execute(f"INSERT INTO {RESET_STAMP_TABLE} VALUES (?, ?, ?)",
                                (stamp["reset_id"], stamp["requested_by"], stamp["requested_at"]))
            except sqlite3.Error as exc:
                raise ResetError(f"Reset stamp failed ({exc}); working copy left unchanged.") from exc
        with closing(connect_ro(original)) as con_o, closing(connect_ro(staging)) as con_s:
            original_names = {name for _, name, _ in schema_objects(con_o)}
            original_digest = content_digest(con_o)
            staged_digest = content_digest(con_s, only=original_names)
            staged_ok = integrity_ok(con_s)
            extension_present = sorted(
                n for (n,) in con_s.execute("SELECT name FROM sqlite_master WHERE type = 'table'") if n in EXTENSION_TABLES)
        problems = []
        if staged_digest != original_digest:
            problems.append("original tables differ in the prepared copy")
        if not staged_ok:
            problems.append("prepared copy failed integrity_check")
        if extension_present != sorted(EXTENSION_TABLES):
            problems.append(f"extension tables missing: {extension_present}")
        if problems:
            raise ResetError("; ".join(problems) + "; working copy left unchanged.")
        staged_sha = sha256_file(staging)

        _ensure_not_in_use(working, ResetError)
        removed = _remove_stale_journals(working)
        try:
            os.replace(staging, working)
        except PermissionError as exc:
            raise ResetError(f"Working copy is in use and could not be replaced ({exc}). Close programs using it and retry.") from exc

        original_after = sha256_file(original)
        if original_after != original_before:
            raise ResetError("ORIGINAL DATABASE HASH CHANGED")
        if sha256_file(working) != staged_sha:
            raise ResetError("working copy does not match the verified prepared copy after the swap")
    finally:
        staging.unlink(missing_ok=True)

    return {
        "event": "db_reset",
        "mode": paths.mode,
        "working_db": str(working),
        "original_sha256": original_after,
        "working_sha256_before_extension": copied,
        "working_sha256_after_extension": staged_sha,
        "original_tables_digest": original_digest,
        "original_tables_unchanged": True,
        "extension_tables": extension_present,
        "stale_journals_removed": removed,
        "reset_id": stamp["reset_id"] if stamp else None,
    }


def snapshot_runtime(paths: Paths) -> dict:
    """Copy the runtime working copy into data/campus_customs_new.db (runtime mode only)."""
    if paths.mode != "runtime":
        raise SnapshotError("Not in runtime mode: the active working copy already is data/campus_customs_new.db.")
    lock = _hold_run_lock(paths, SnapshotError)
    try:
        return _snapshot_locked(paths)
    finally:
        lock.__exit__(None, None, None)


def _snapshot_locked(paths: Paths) -> dict:
    source, target = paths.working_db, paths.repo_working_db
    if not source.exists():
        raise SnapshotError(f"Runtime working copy not found: {source}. Run the reset first.")
    assert_writable(target, paths)

    staging = target.with_name(target.name + ".snapshotting")
    staging.unlink(missing_ok=True)
    with closing(connect_ro(source)) as src, closing(sqlite3.connect(staging)) as dst:
        src.backup(dst)                          # consistent copy of committed data only
        source_digest = content_digest(src)
    with closing(connect_ro(staging)) as con:
        staged_digest, staged_ok = content_digest(con), integrity_ok(con)
    if staged_digest != source_digest or not staged_ok:
        staging.unlink(missing_ok=True)
        raise SnapshotError("Snapshot verification failed; data/campus_customs_new.db left unchanged.")

    try:
        _ensure_not_in_use(target, SnapshotError)
    except SnapshotError:
        staging.unlink(missing_ok=True)
        raise
    removed = _remove_stale_journals(target)
    try:
        os.replace(staging, target)
    except PermissionError as exc:
        staging.unlink(missing_ok=True)
        raise SnapshotError(f"Target is in use and could not be replaced ({exc}).") from exc

    return {
        "event": "db_snapshot",
        "source": str(source),
        "target": str(target),
        "content_digest": source_digest,
        "target_sha256": sha256_file(target),
        "stale_journals_removed": removed,
    }
