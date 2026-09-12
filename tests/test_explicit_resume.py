"""Explicit actions bypass language classification, never task authority."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from context_agent.config import AppConfig, ProviderConfig
from context_agent.diagnostics import DiagnosticStore
from context_agent.routing import route_chat_request
from context_agent.task_state import TaskConflict, TaskStateStore
from context_agent.web import create_app


def prepare(store, root, task, **overrides):
    def forbidden(*args):
        raise AssertionError("Explicit actions must not call the classifier")

    options = dict(
        query="Unrelated quoted content cannot select another task",
        thread="test",
        workspace=root,
        mode="agent",
        execution="auto",
        allow_write=True,
        owner="worker",
        lease_seconds=60,
        task_id=task.id,
        explicit_action="continue",
        expected_revision=task.revision,
        semantic=forbidden,
    )
    options.update(overrides)
    return store.prepare_turn(**options)


def test_explicit_resume_and_duplicate_fencing(tmp_path):
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        task = store.create(
            "test",
            tmp_path,
            "Fix project code",
            route_chat_request("Fix project code"),
            True,
        )
        claimed, route = prepare(store, tmp_path, task)
        assert claimed.id == task.id
        assert route.intent["reason"] == "EXPLICIT_ACTION"
        with pytest.raises(TaskConflict, match="STALE_TASK_REVISION"):
            prepare(store, tmp_path, task)


@pytest.mark.parametrize(
    "change",
    [{"thread": "other"}, {"expected_revision": 99}, {"explicit_action": "override"}],
)
def test_invalid_explicit_resume(tmp_path, change):
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        task = store.create(
            "test",
            tmp_path,
            "Fix project code",
            route_chat_request("Fix project code"),
            True,
        )
        with pytest.raises(TaskConflict):
            prepare(store, tmp_path, task, **change)


def test_readonly_and_reconciliation(tmp_path):
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        task = store.create(
            "test", tmp_path, "Run pytest", route_chat_request("Run pytest"), False
        )
        claimed, route = prepare(store, tmp_path, task)
        assert not route.mutation_requested
        store.finish(claimed, "blocked", "reconciliation_required: missing link")
        blocked = store.resolve("test", tmp_path, task.id)
        with pytest.raises(TaskConflict, match="EXECUTION_LINK_UNVERIFIED"):
            prepare(store, tmp_path, blocked)


def test_api_rejected_resume_is_durable(tmp_path):
    config = AppConfig(
        project_root=tmp_path,
        context_root=tmp_path / "workspace",
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
    )
    provider = ProviderConfig(
        name="lmstudio",
        model="offline",
        api_key="test",
        base_url="http://127.0.0.1:1234/v1",
    )
    with TestClient(create_app(config, (provider,))) as client:
        csrf = client.get("/api/runtime").json()["csrf_token"]
        response = client.post(
            "/api/tasks/" + "a" * 32 + "/resume",
            json={"expected_revision": 1},
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 409
        assert response.json()["diagnostic_id"]
        with DiagnosticStore(config.diagnostics_database) as journal:
            records = journal.list_requests(limit=10)
            assert any(record["error_code"] == "task_not_found" for record in records)


@pytest.mark.parametrize("mode", ["agent", "debug", "multitask"])
@pytest.mark.parametrize("scope", ["project", "file"])
def test_explicit_verify_corrects_legacy_flags_without_write_or_scope_expansion(
    tmp_path, mode, scope
):
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        legacy = replace(
            route_chat_request("verification-only /workspace/project/pyproject.toml"),
            allow_project_checks=False,
            allow_project_scan=True,
            mutation_requested=True,
            scope=scope,
        )
        task = store.create("test", tmp_path, "Verify existing code", legacy, True)
        claimed, route = prepare(
            store,
            tmp_path,
            task,
            explicit_action="continue_verification",
            mode=mode,
        )
        assert claimed.id == task.id
        assert route.workflow == "verification-only"
        assert route.execution == "persistent"
        assert route.scope == scope
        assert route.allow_project_checks
        assert not route.allow_project_scan
        assert not route.mutation_requested
        assert "EXPLICIT_VERIFY_ONLY" in route.reason_codes
        # Routing normalization is per-action; original authority stays immutable.
        assert claimed.routing["allow_project_checks"] is False
        assert claimed.routing["allow_project_scan"] is True


@pytest.mark.parametrize("mode", ["ask", "plan"])
def test_explicit_verification_is_denied_in_read_or_plan_mode_without_claim(
    tmp_path, mode
):
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        task = store.create(
            "test",
            tmp_path,
            "Verify existing code",
            route_chat_request("verification-only /workspace/project/pyproject.toml"),
            False,
        )
        before = store.resolve("test", tmp_path, task.id)
        with pytest.raises(TaskConflict, match="ACTION_NOT_AVAILABLE_IN_MODE"):
            prepare(
                store,
                tmp_path,
                task,
                explicit_action="continue_verification",
                mode=mode,
            )
        assert store.resolve("test", tmp_path, task.id) == before
        assert before.owner is None
