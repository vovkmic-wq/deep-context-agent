"""Immutable verification evidence must survive restart and remain scoped."""

import sqlite3

import pytest

from context_agent.project_checks import ProjectCheckRunner
from context_agent.verification_context import (
    VerificationContextStore,
    source_fingerprint,
)


@pytest.mark.parametrize("filename", ["uv.lock", "poetry.lock", "requirements.txt"])
def test_dependency_file_drift_changes_fingerprint(tmp_path, filename):
    target = tmp_path / filename
    target.write_text("old")
    before = source_fingerprint(tmp_path)
    target.write_text("new")
    assert source_fingerprint(tmp_path) != before


def test_context_roundtrip_is_scoped_and_deduplicated(tmp_path):
    database = tmp_path / "context.sqlite3"
    payload = {"project_root": "/workspace/ozon", "checks": ["pytest"]}
    with VerificationContextStore(database) as store:
        first = store.save(tmp_path, payload)
        assert store.save(tmp_path, payload) == first
    payload["checks"].append("compileall")
    with VerificationContextStore(database) as store:
        assert store.load(tmp_path, first)["checks"] == ["pytest"]
        with pytest.raises(ValueError, match="unavailable"):
            store.load(tmp_path / "other", first)
        assert store.save(tmp_path, payload) != first


def test_restored_root_does_not_choose_other_manifest(tmp_path):
    database = tmp_path / "state.sqlite3"
    nested = tmp_path / "ozon"
    nested.mkdir()
    (nested / "pyproject.toml").write_text("[project]\nname='ozon'\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='other'\n")
    with VerificationContextStore(database) as store:
        identity = store.save(tmp_path, {"project_root": "/workspace/ozon"})
    runner = ProjectCheckRunner(
        workspace=tmp_path,
        timeout_seconds=10,
        output_max_chars=1000,
        context_database=database,
    )
    assert runner.restore_project_root(identity) == nested.resolve()
    with pytest.raises(ValueError, match="conflict"):
        runner.restore_project_root(identity, project_root=tmp_path)


def test_corrupt_context_cannot_be_used_as_evidence(tmp_path):
    database = tmp_path / "context.sqlite3"
    with VerificationContextStore(database) as store:
        identity = store.save(tmp_path, {"checks": ["pytest"]})
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE verification_contexts SET payload='{}'")
    with (
        VerificationContextStore(database) as store,
        pytest.raises(ValueError, match="integrity"),
    ):
        store.load(tmp_path, identity)
