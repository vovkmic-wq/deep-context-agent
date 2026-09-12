"""Verification contexts are immutable evidence, not reusable authority."""

import json
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from context_agent.verification_context import (
    VerificationContext,
    VerificationContextStore,
    VerificationInvocation,
    capture_context,
    source_fingerprint,
)


def fixture_context(root):
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    return capture_context(
        root,
        root,
        str(root / ".venv/Scripts/python.exe"),
        {
            "version": "3.12",
            "prefix": "env",
            "base_prefix": "base",
            "tools": {"pytest": True},
            "tool_versions": {"pytest": "8.0"},
            "dependencies_sha256": "a" * 64,
            "origins": {},
            "origin_status": "no_packages_discovered",
        },
        ("pytest",),
        source_fingerprint(root),
        invocation=VerificationInvocation(
            task_id="task",
            job_id="job",
            request_id="request",
            task_revision=3,
            job_generation=2,
        ),
        root_source="explicit",
        partial=True,
    )


def test_typed_context_roundtrip_and_compact_receipt(tmp_path):
    context = fixture_context(tmp_path)
    with VerificationContextStore(tmp_path / "state.db") as store:
        identity = store.save(tmp_path, context.payload())
        restored = VerificationContext.from_payload(store.load(tmp_path, identity))
    assert restored == context
    assert identity == context.context_id
    assert context.invocation.task_id == "task"
    assert context.configuration_hashes[0][0] == "pyproject.toml"
    assert context.launch_reference.endswith("python.exe")
    assert context.prefix == "env"
    assert context.invocation.checks_allowed
    assert not context.invocation.write_allowed
    assert "prefix" not in context.receipt()
    assert "launch_reference" not in context.receipt()
    with pytest.raises(FrozenInstanceError):
        context.prefix = "other"


@pytest.mark.parametrize("field", ["invocation", "source_sha256", "captured_at"])
def test_missing_typed_fields_fail_closed(tmp_path, field):
    payload = fixture_context(tmp_path).payload()
    del payload[field]
    with pytest.raises(ValueError):
        VerificationContext.from_payload(payload)


def test_context_run_identity_changes_but_fingerprint_remains(tmp_path):
    first = fixture_context(tmp_path)
    second = fixture_context(tmp_path)
    assert first.context_id != second.context_id
    assert first.run_id != second.run_id
    assert first.source_sha256 == second.source_sha256
    assert json.loads(json.dumps(first.payload())) == first.payload()


def test_invalid_schema_not_silently_treated_as_legacy(tmp_path):
    payload = fixture_context(tmp_path).payload()
    payload["schema_version"] = 99
    with (
        VerificationContextStore(tmp_path / "state.db") as store,
        pytest.raises(ValueError, match="schema"),
    ):
        store.save(tmp_path, payload)


def test_controller_restores_context_before_discovery(tmp_path, monkeypatch):
    from context_agent.project_checks import ProjectCheckRunner
    from context_agent.runtime import AgentRuntime

    project = tmp_path / "ozon"
    project.mkdir()
    context = replace(fixture_context(project), project_root="/workspace/ozon")
    database = tmp_path / "state.db"
    with VerificationContextStore(database) as store:
        identity = store.save(tmp_path, context.payload())
    (tmp_path / "pyproject.toml").touch()  # A new competing root after restart.
    runtime = object.__new__(AgentRuntime)
    runtime.app_config = SimpleNamespace(workspace=tmp_path)
    runtime.project_check_runner = ProjectCheckRunner(
        workspace=tmp_path,
        context_database=database,
        timeout_seconds=5,
        output_max_chars=1000,
    )
    runtime.autopilot_store = SimpleNamespace(
        details=lambda _: {
            "verification_results": [
                {"verification_context": {"persisted_context_id": identity}}
            ]
        }
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("saved root must win before discovery")

    monkeypatch.setattr("context_agent.runtime.resolve_project_root", forbidden)
    assert runtime._resolve_verification_root("job", "Continue") == project
    with pytest.raises(ValueError, match="conflict"):
        runtime._resolve_verification_root("job", "Continue", tmp_path)


def test_context_snapshot_cannot_enable_checks(tmp_path):
    from context_agent.project_checks import ProjectCheckRunner

    runner = ProjectCheckRunner(
        workspace=tmp_path,
        timeout_seconds=5,
        output_max_chars=1000,
        invocation=lambda: VerificationInvocation(checks_allowed=False),
    )
    with pytest.raises(ValueError, match="not permitted"):
        runner.run("compileall")
