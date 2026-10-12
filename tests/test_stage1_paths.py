"""Stage 1, group S: centralised paths and runtime configuration."""

import pytest

import paths
from conftest import REAL_ROOT


def test_s1_default_layout_needs_no_configuration(sandbox):
    p = paths.resolve(env={}, root=sandbox, use_dotenv=False)
    assert p.mode == "default"
    assert p.original_db == sandbox / "data" / "campus_customs.db"
    assert p.working_db == sandbox / "data" / "campus_customs_new.db"
    assert p.audit_db == sandbox / "data" / "audit.db"
    assert p.audit_trail_json == sandbox / "output" / "audit_trail.json"
    assert p.repo_working_db == p.working_db


def test_s1_fresh_clone_resolves_to_assignment_paths():
    # The real repository with no environment and no .env: exactly the assignment's data/ paths.
    p = paths.resolve(env={}, use_dotenv=False)
    assert p.mode == "default"
    assert p.working_db == REAL_ROOT / "data" / "campus_customs_new.db"
    assert p.original_db == REAL_ROOT / "data" / "campus_customs.db"


def test_s2_runtime_override_moves_only_active_files(sandbox, runtime_dir):
    p = paths.resolve(env={"CC_RUNTIME_DIR": str(runtime_dir)}, root=sandbox, use_dotenv=False)
    assert p.mode == "runtime"
    assert p.working_db == runtime_dir.resolve() / "campus_customs_new.db"
    assert p.audit_db == runtime_dir.resolve() / "audit.db"
    # The original, the snapshot target and the required JSON trail stay in the repository.
    assert p.original_db == sandbox / "data" / "campus_customs.db"
    assert p.repo_working_db == sandbox / "data" / "campus_customs_new.db"
    assert p.audit_trail_json == sandbox / "output" / "audit_trail.json"


def test_s2_dotenv_is_read_and_process_environment_wins(sandbox, tmp_path):
    from_file, from_env = tmp_path / "from_file", tmp_path / "from_env"
    (sandbox / ".env").write_text(f"PORTKEY_API_KEY=dummy-not-a-key\nCC_RUNTIME_DIR={from_file}\n")
    assert paths.resolve(env={}, root=sandbox).working_db.parent == from_file.resolve()
    assert paths.resolve(env={"CC_RUNTIME_DIR": str(from_env)}, root=sandbox).working_db.parent == from_env.resolve()


def test_s2_environment_variables_in_the_setting_are_expanded(sandbox, tmp_path, monkeypatch):
    monkeypatch.setenv("CC_TEST_BASE", str(tmp_path))
    p = paths.resolve(env={"CC_RUNTIME_DIR": "$CC_TEST_BASE/rt"}, root=sandbox, use_dotenv=False)
    assert p.working_db.parent == (tmp_path / "rt").resolve()


@pytest.mark.parametrize("bad", ["relative/dir", "$CC_UNDEFINED_VAR_FOR_TEST/rt"])
def test_s2_unusable_setting_is_refused(sandbox, bad):
    with pytest.raises(paths.PathConfigError):
        paths.resolve(env={"CC_RUNTIME_DIR": bad}, root=sandbox, use_dotenv=False)


def test_s2_blank_or_data_dir_setting_means_default(sandbox):
    assert paths.resolve(env={"CC_RUNTIME_DIR": "  "}, root=sandbox, use_dotenv=False).mode == "default"
    p = paths.resolve(env={"CC_RUNTIME_DIR": str(sandbox / "data")}, root=sandbox, use_dotenv=False)
    assert p.mode == "default" and p.working_db == p.repo_working_db


def test_s5_original_is_never_a_write_target(sandbox):
    p = paths.resolve(env={}, root=sandbox, use_dotenv=False)
    for alias in (p.original_db, sandbox / "data" / ".." / "data" / "campus_customs.db"):
        with pytest.raises(paths.UnsafeWriteError):
            paths.assert_writable(alias, p)
    paths.assert_writable(p.working_db, p)  # the working copy is allowed
