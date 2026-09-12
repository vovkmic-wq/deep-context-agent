"""Failures between explicit reservation and worker dispatch are never replayed."""

from __future__ import annotations

from fastapi.testclient import TestClient

from context_agent.config import AppConfig, ProviderConfig
from context_agent.diagnostics import DiagnosticStore
from context_agent.routing import route_chat_request
from context_agent.task_actions import TaskActionStore
from context_agent.task_state import TaskStateStore
from context_agent.web import create_app


def setup(tmp_path):
    config = AppConfig(
        project_root=tmp_path,
        context_root=tmp_path / "workspace",
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
    )
    nested = config.workspace / "nested"
    nested.mkdir(parents=True)
    (nested / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.0.1'\n", encoding="utf-8"
    )
    with TaskStateStore(config.context_database) as store:
        task = store.create(
            "selected",
            config.workspace,
            "verification-only /workspace/nested/pyproject.toml",
            route_chat_request("verification-only /workspace/nested/pyproject.toml"),
            False,
        )
        task = store.resolve("selected", config.workspace, task.id)
    provider = ProviderConfig(
        name="lmstudio",
        model="offline-test",
        api_key="test",
        base_url="http://127.0.0.1:9/v1",
    )
    return config, task, provider


def auth(client):
    return {"x-csrf-token": client.get("/api/runtime").json()["csrf_token"]}


def forbidden(*_args, **_kwargs):
    raise AssertionError("Crash replay must not dispatch a worker or classifier")


def request_body(task, *, verification=False):
    body = {
        "thread_id": "selected",
        "expected_revision": task.revision,
        "idempotency_key": "dispatch-recovery-123",
    }
    if verification:
        body.update(project_root="/workspace/nested", confirmed=True)
    else:
        body.update(action="continue_verification")
    return body


def test_generic_failure_after_reservation_has_terminal_diagnostic(
    tmp_path, monkeypatch
):
    config, source, provider = setup(tmp_path)

    def fail_materialization(*_args, **_kwargs):
        raise RuntimeError("Injected private detail before worker launch")

    monkeypatch.setattr(
        TaskActionStore, "create_linked_verification", fail_materialization
    )
    monkeypatch.setattr("context_agent.web._runtime_factory", forbidden)
    monkeypatch.setattr("context_agent.web.semantic_classifier", forbidden)
    with TestClient(create_app(config, (provider,))) as client:
        response = client.post(
            f"/api/tasks/{source.id}/verification",
            headers=auth(client),
            json=request_body(source, verification=True),
        )
        assert response.status_code == 503, response.text
        error = response.json()["error"]
        assert error["code"] == "ACTION_DISPATCH_UNCONFIRMED"
        assert "private detail" not in response.text
        with DiagnosticStore(config.diagnostics_database) as store:
            record = store.request(error["diagnostic_id"])
            assert record["status"] == "failed"
            assert record["error_code"] == "action_dispatch_unconfirmed"
        with TaskActionStore(
            config.context_database, workspace=config.workspace
        ) as store:
            record = store.db.execute("SELECT * FROM explicit_task_actions").fetchone()
            assert record["status"] == "failed"
            assert (
                store.db.execute("SELECT COUNT(*) FROM authorized_tasks").fetchone()[0]
                == 1
            )


def test_cas_failure_after_reservation_preserves_exact_error_code(
    tmp_path, monkeypatch
):
    config, source, provider = setup(tmp_path)
    original = TaskActionStore.reserve

    def concurrent_claim(store, **kwargs):
        reservation = original(store, **kwargs)
        if reservation.created:
            with TaskStateStore(config.context_database) as state:
                state.claim(source, "concurrent-controller", 30)
        return reservation

    monkeypatch.setattr(TaskActionStore, "reserve", concurrent_claim)
    monkeypatch.setattr("context_agent.web._runtime_factory", forbidden)
    monkeypatch.setattr("context_agent.web.semantic_classifier", forbidden)
    with TestClient(create_app(config, (provider,))) as client:
        response = client.post(
            f"/api/tasks/{source.id}/resume",
            headers=auth(client),
            json=request_body(source),
        )
        assert response.status_code == 409, response.text
        error = response.json()["error"]
        assert error["code"] == "STALE_TASK_REVISION"
        with DiagnosticStore(config.diagnostics_database) as journal:
            record = journal.request(error["diagnostic_id"])
            assert record["status"] == "failed"
            assert record["error_code"] == "stale_task_revision"
        with TaskStateStore(config.context_database) as state:
            current = state.resolve("selected", config.workspace, source.id)
            assert current.owner == "concurrent-controller"
            assert current.revision == source.revision + 1


def test_reserved_replay_recovers_existing_web_execution_without_dispatch(
    tmp_path, monkeypatch
):
    config, source, provider = setup(tmp_path)
    with TaskActionStore(config.context_database, workspace=config.workspace) as store:
        reservation = store.reserve(
            task_id=source.id,
            thread="selected",
            action="continue_verification",
            expected_revision=source.revision,
            idempotency_key="dispatch-recovery-123",
        )
    with DiagnosticStore(config.diagnostics_database) as journal:
        journal.record_task_start(reservation.execution_id, "chat_autopilot")
        journal.record_task_terminal(
            reservation.execution_id,
            {"event": "blocked", "data": {"message": "Fixture completed safely"}},
        )
    monkeypatch.setattr("context_agent.web._runtime_factory", forbidden)
    monkeypatch.setattr("context_agent.web.semantic_classifier", forbidden)
    for _restart in range(2):
        with TestClient(create_app(config, (provider,))) as client:
            response = client.post(
                f"/api/tasks/{source.id}/resume",
                headers=auth(client),
                json=request_body(source),
            )
            assert response.status_code == 202, response.text
            result = response.json()
            assert result["task_id"] == reservation.execution_id
            assert result["active_task_id"] == source.id
            assert result["status"] == "blocked"
            assert result["reused"] is True
    with TaskStateStore(config.context_database) as state:
        current = state.resolve("selected", config.workspace, source.id)
        assert current.revision == source.revision
        assert current.owner is None


def test_materialized_child_before_dispatch_is_recoverable_without_worker(
    tmp_path, monkeypatch
):
    config, source, provider = setup(tmp_path)
    with TaskActionStore(config.context_database, workspace=config.workspace) as store:
        reservation = store.reserve(
            task_id=source.id,
            thread="selected",
            action="create_verification",
            expected_revision=source.revision,
            idempotency_key="dispatch-recovery-123",
            project_root="/workspace/nested",
        )
        child = store.create_linked_verification(reservation)
    monkeypatch.setattr("context_agent.web._runtime_factory", forbidden)
    monkeypatch.setattr("context_agent.web.semantic_classifier", forbidden)
    for _restart in range(2):
        with TestClient(create_app(config, (provider,))) as client:
            response = client.post(
                f"/api/tasks/{source.id}/verification",
                headers=auth(client),
                json=request_body(source, verification=True),
            )
            assert response.status_code == 202, response.text
            result = response.json()
            assert result["active_task_id"] == child.id
            assert result["reason_code"] == "ACTION_DISPATCH_UNCONFIRMED"
            assert result["reused"] is True
            assert "task_id" not in result
            with DiagnosticStore(config.diagnostics_database) as journal:
                assert journal.task(reservation.execution_id) is None
    with TaskStateStore(config.context_database) as state:
        tasks = state.list("selected", config.workspace)
        assert len(tasks) == 2
        assert all(task.owner is None for task in tasks)
        assert all(task.revision == 1 for task in tasks)
        assert state.resolve("selected", config.workspace, source.id) == source
