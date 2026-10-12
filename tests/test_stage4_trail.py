"""Stage 4, group J: the append-preserving JSON audit trail (temporary files only)."""

import json
import os
import subprocess
import sys
import time
import uuid

import pytest

import audit_trail
import locks
from conftest import REAL_ROOT

PYTHON = sys.executable
RUN = str(uuid.uuid4())


@pytest.fixture
def trail(tmp_path):
    return audit_trail.AuditTrail(tmp_path / "output" / "audit_trail.json")


def ev(i=0, **over):
    fields = dict(actor="inventory", actor_kind="agent", mode="simulated", run_id=RUN, details={"i": i})
    fields.update(over)
    return audit_trail.make_event(fields.pop("event_type", "agent_started"), **fields)


def test_j1_new_file_is_a_valid_json_array_with_a_genesis_link(trail):
    stored = trail.append([ev(1)])
    data = json.loads(trail.path.read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["seq"] == 1 and data[0]["run_seq"] == 1 and data[0]["prev_hash"] == audit_trail.GENESIS
    assert data[0] == stored[0] and trail.verify() == {"events": 1, "valid": True, "problems": [], "exists": True}


def test_j2_appending_preserves_earlier_history_byte_for_byte(trail):
    trail.append([ev(i) for i in range(3)])
    before_text = trail.path.read_text(encoding="utf-8")
    before = json.loads(before_text)
    trail.append([ev(9, actor="accounting")])
    after_text = trail.path.read_text(encoding="utf-8")
    after = json.loads(after_text)
    assert after[:3] == before                                         # same events, same hashes
    assert after_text.startswith(before_text[: -len("\n]\n")])        # earlier text untouched
    assert after[3]["prev_hash"] == before[2]["hash"]


def test_j3_order_seq_run_seq_and_links(trail):
    other = str(uuid.uuid4())
    trail.append([ev(1), ev(2, run_id=other), ev(3)])
    trail.append([audit_trail.system_event("db_reset", outcome={"status": "ok"}), ev(4, run_id=other)])
    data = trail.read()
    assert [e["seq"] for e in data] == [1, 2, 3, 4, 5]
    assert [e["run_seq"] for e in data] == [1, 1, 2, None, 2]
    assert all(data[i]["prev_hash"] == data[i - 1]["hash"] for i in range(1, 5))
    assert data[3]["actor_kind"] == "system" and data[3]["mode"] is None and data[3]["run_id"] is None


WRITER = r'''
import sys, uuid
sys.path.insert(0, sys.argv[1])
import audit_trail, time
from pathlib import Path
t = audit_trail.AuditTrail(Path(sys.argv[2]))
go = Path(sys.argv[3])
while not go.exists():
    time.sleep(0.005)
for i in range(int(sys.argv[5])):
    t.append([audit_trail.make_event("agent_started", actor="boss", actor_kind="agent", mode="simulated",
                                     run_id=sys.argv[4], details={"i": i})])
'''


def test_j4_concurrent_writers_lose_nothing(trail, tmp_path):
    go = tmp_path / "go"
    runs = [str(uuid.uuid4()) for _ in range(6)]
    procs = [subprocess.Popen([PYTHON, "-c", WRITER, str(REAL_ROOT / "mcp_server"), str(trail.path), str(go), r, "10"])
             for r in runs]
    time.sleep(1.0)
    go.touch()
    assert all(p.wait(timeout=120) == 0 for p in procs)
    data = trail.read()
    assert len(data) == 60 and [e["seq"] for e in data] == list(range(1, 61))
    for r in runs:                                                   # each writer's events all present, in order
        mine = [e["details"]["i"] for e in data if e["run_id"] == r]
        assert mine == list(range(10)) and [e["run_seq"] for e in data if e["run_id"] == r] == list(range(1, 11))
    assert trail.verify()["valid"]


CRASH_BEFORE_REPLACE = r'''
import os, sys, uuid
sys.path.insert(0, sys.argv[1])
import audit_trail
from pathlib import Path
audit_trail.os.replace = lambda *a: os._exit(1)        # die after the temp file is written, before the swap
audit_trail.AuditTrail(Path(sys.argv[2])).append([audit_trail.make_event(
    "agent_started", actor="boss", actor_kind="agent", mode="simulated", run_id=str(uuid.uuid4()))])
'''


def test_j5_crash_mid_write_leaves_the_previous_file_intact(trail):
    trail.append([ev(i) for i in range(2)])
    before = trail.path.read_bytes()
    proc = subprocess.run([PYTHON, "-c", CRASH_BEFORE_REPLACE, str(REAL_ROOT / "mcp_server"), str(trail.path)])
    assert proc.returncode == 1
    assert trail.path.read_bytes() == before and trail.verify()["valid"]
    leftovers = list(trail.path.parent.glob("audit_trail.json.tmp-*"))
    assert leftovers, "precondition: the crash leaves a temporary file"
    trail.append([ev(5)])                                            # next writer recovers
    assert not list(trail.path.parent.glob("audit_trail.json.tmp-*"))
    assert len(trail.read()) == 3


CRASH_HOLDING_LOCK = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
import locks
from pathlib import Path
cm = locks.file_lock(Path(sys.argv[2])); cm.__enter__(); os._exit(1)
'''


def test_j6_lock_of_a_crashed_writer_is_released_by_the_os(trail):
    proc = subprocess.run([PYTHON, "-c", CRASH_HOLDING_LOCK, str(REAL_ROOT / "mcp_server"), str(trail.lock_path)])
    assert proc.returncode == 1 and trail.lock_path.exists()
    started = time.monotonic()
    trail.append([ev(1)])
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("damage", ["edit_field", "truncate", "delete_event", "not_a_list"])
def test_j7_damaged_trail_is_refused_and_left_untouched(trail, damage):
    trail.append([ev(i) for i in range(3)])
    data = json.loads(trail.path.read_text(encoding="utf-8"))
    if damage == "edit_field":
        data[1]["actor"] = "accounting"; text = json.dumps(data, indent=2)      # noqa: E702
    elif damage == "truncate":
        text = trail.path.read_text(encoding="utf-8")[:-40]
    elif damage == "delete_event":
        del data[1]; text = json.dumps(data, indent=2)                           # noqa: E702
    else:
        text = json.dumps({"events": data})
    trail.path.write_text(text, encoding="utf-8")
    damaged = trail.path.read_bytes()
    with pytest.raises(audit_trail.AuditTrailCorrupt):
        trail.append([ev(9)])
    assert trail.path.read_bytes() == damaged
    assert not trail.verify()["valid"]


def test_j8_write_failure_is_reported_and_changes_nothing(trail, monkeypatch):
    trail.append([ev(1)])
    before = trail.path.read_bytes()
    def fail(*a):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(audit_trail.os, "replace", fail)
    with pytest.raises(audit_trail.AuditTrailError, match="simulated disk failure"):
        trail.append([ev(2)])
    assert trail.path.read_bytes() == before
    assert not list(trail.path.parent.glob("audit_trail.json.tmp-*"))


def test_j9_lock_timeout_is_an_error_not_a_silent_skip(tmp_path):
    t = audit_trail.AuditTrail(tmp_path / "audit_trail.json", lock_timeout=0.2)
    with locks.file_lock(t.lock_path):
        with pytest.raises(audit_trail.AuditTrailError, match="Could not lock"):
            t.append([ev(1)])
    assert not t.path.exists()


def test_j10_duplicates_retries_and_batch_atomicity(trail):
    e = ev(1)
    trail.append([e])
    assert trail.append([dict(e)])[0]["seq"] == 1                  # a retry is not stored twice
    assert len(trail.read()) == 1
    with pytest.raises(audit_trail.DuplicateEventConflict):
        trail.append([{**e, "details": {"i": 999}}])
    with pytest.raises(audit_trail.AuditTrailError):
        trail.append([ev(2), ev(3, event_type="made_up_event")])
    assert len(trail.read()) == 1                                    # nothing from the failed batch


@pytest.mark.parametrize("bad", [
    dict(event_type="made_up_event"),
    dict(actor="ceo"),                                              # forged agent name
    dict(mode=None),                                                # agent event without live/simulated label
    dict(run_id=None),
    dict(actor_kind="system"),                                      # system event must have actor "system"
    dict(actor_kind="human", actor="Ryan"),
    dict(mode="production"),
    dict(outcome={"status": "rolled_back"}),
    dict(details={"x": object()}),
])
def test_j11_invalid_events_are_rejected(trail, bad):
    with pytest.raises(audit_trail.AuditTrailError):
        trail.append([ev(1, **bad)])
    assert not trail.path.exists()


def test_j11_writer_fields_cannot_be_supplied_by_callers(trail):
    for field in ("seq", "hash", "prev_hash", "run_seq"):
        with pytest.raises(audit_trail.AuditTrailError, match="writer-only"):
            trail.append([{**ev(1), field: 1}])


def test_j11_credentials_are_never_recorded(trail, monkeypatch):
    monkeypatch.setenv("PORTKEY_API_KEY", "pk-test-0123456789")
    with pytest.raises(audit_trail.AuditTrailError, match="credential"):
        trail.append([ev(1, args={"header": "Bearer pk-test-0123456789"})])
    assert not trail.path.exists()


def test_j12_usage_is_recorded_only_when_supplied(trail):
    rec = audit_trail.RunRecorder(trail, RUN, "simulated")
    rec.run_started(model="gpt-6-luna")
    no_usage = rec.run_finished(status="completed")
    with_usage = rec.run_finished(status="stopped", usage={"requests": 3, "input_tokens": 120, "output_tokens": 40})
    assert no_usage["usage"] is None
    assert with_usage["usage"] == {"requests": 3, "input_tokens": 120, "output_tokens": 40}
    assert with_usage["outcome"] == {"status": "error", "run_status": "stopped"}


def test_j13_simulated_and_live_are_labelled(trail):
    audit_trail.RunRecorder(trail, str(uuid.uuid4()), "simulated").record("agent_started", "boss")
    audit_trail.RunRecorder(trail, str(uuid.uuid4()), "live").record("agent_started", "boss")
    assert [e["mode"] for e in trail.read()] == ["simulated", "live"]
    with pytest.raises(ValueError):
        audit_trail.RunRecorder(trail, str(uuid.uuid4()), "demo")
    with pytest.raises(ValueError):
        audit_trail.RunRecorder(trail, "not-a-uuid", "live")


def test_j14_transient_permission_error_on_swap_is_retried(trail, monkeypatch):
    """Precaution for Windows scanners briefly holding the file (not observed in our runs); simulated here."""
    trail.append([ev(1)])
    real_replace, failures = audit_trail.os.replace, []

    def flaky(src, dst):
        if len(failures) < 3:
            failures.append(1)
            raise PermissionError(13, "Permission denied")
        return real_replace(src, dst)
    monkeypatch.setattr(audit_trail.os, "replace", flaky)
    trail.append([ev(2)])
    assert len(failures) == 3 and [e["details"]["i"] for e in trail.read()] == [1, 2]
    data = trail.read()                                              # ordering and chain intact after retries
    assert [e["seq"] for e in data] == [1, 2] and data[1]["prev_hash"] == data[0]["hash"] and trail.verify()["valid"]
    assert not list(trail.path.parent.glob("audit_trail.json.tmp-*"))


def test_j14_persistent_permission_error_still_fails_closed(trail, monkeypatch):
    trail.append([ev(1)])
    before = trail.path.read_bytes()
    monkeypatch.setattr(audit_trail.time, "sleep", lambda s: None)
    def always(src, dst):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(audit_trail.os, "replace", always)
    with pytest.raises(audit_trail.AuditTrailError, match="Permission denied"):
        trail.append([ev(2)])
    assert trail.path.read_bytes() == before and not list(trail.path.parent.glob("audit_trail.json.tmp-*"))


HOLD_EMPTY_LOCK = r'''
import msvcrt, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)
msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)            # lock byte 0 of an EMPTY file, as locks.py now does
open(sys.argv[2], "w").close()                    # signal: lock held
time.sleep(3)
'''


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range locks are mandatory; POSIX flock is advisory")
def test_j15_waiting_on_a_held_empty_lock_file_times_out_cleanly(tmp_path):
    """Regression for the bug found on 2026-10-09: a second process WROTE into a new lock file whose byte
    0 another process had locked, and Windows raised 'Permission denied' instead of waiting."""
    lock, ready = tmp_path / "x.lock", tmp_path / "ready"
    holder = subprocess.Popen([PYTHON, "-c", HOLD_EMPTY_LOCK, str(lock), str(ready)])
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert lock.stat().st_size == 0
        with pytest.raises(locks.LockTimeout):
            with locks.file_lock(lock, timeout=0.3):
                pass
    finally:
        holder.wait(timeout=30)


HOLD_AND_STAMP = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
import locks
from pathlib import Path
go = Path(sys.argv[3])
while not go.exists():
    time.sleep(0.002)
with open(sys.argv[4], "w") as out:
    for _ in range(5):
        with locks.file_lock(Path(sys.argv[2]), timeout=60):
            start = time.time_ns(); time.sleep(0.02); end = time.time_ns()
        out.write(f"{start} {end}\n")
"""


def test_j16_lock_is_exclusive_across_competing_processes_on_a_new_lock_file(tmp_path):
    lock, go = tmp_path / "fresh.lock", tmp_path / "go"
    assert not lock.exists()                                          # every process races to create it
    outs = [tmp_path / f"p{i}.txt" for i in range(6)]
    procs = [subprocess.Popen([PYTHON, "-c", HOLD_AND_STAMP, str(REAL_ROOT / "mcp_server"), str(lock), str(go), str(o)])
             for o in outs]
    time.sleep(1.0)
    go.touch()
    assert all(p.wait(timeout=120) == 0 for p in procs)
    spans = sorted(tuple(map(int, line.split())) for o in outs for line in o.read_text().splitlines())
    assert len(spans) == 30
    assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1)), "two processes held the lock at once"


CRASH_HOLDING_RUN_LOCK = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import locks
from pathlib import Path
cm = locks.file_lock(locks.run_lock_path(Path(sys.argv[2])), timeout=0); cm.__enter__(); os._exit(1)
"""


def test_j17_a_crashed_run_does_not_block_reset_or_snapshot(tmp_path):
    proc = subprocess.run([PYTHON, "-c", CRASH_HOLDING_RUN_LOCK, str(REAL_ROOT / "mcp_server"), str(tmp_path)])
    assert proc.returncode == 1 and locks.run_lock_path(tmp_path).exists()
    assert locks.run_active(tmp_path) is False
