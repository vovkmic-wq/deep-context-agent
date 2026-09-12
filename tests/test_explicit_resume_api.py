"""Public explicit actions must bypass classification, not authorization."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from context_agent.autopilot import AutopilotStore
from context_agent.config import AppConfig, ProviderConfig
from context_agent.diagnostics import DiagnosticStore
from context_agent.routing import route_chat_request
from context_agent.task_state import TaskStateStore
from context_agent.web import create_app


def configuration(tmp_path: Path) -> AppConfig:
    config = AppConfig(
        project_root=tmp_path,
        context_root=tmp_path / "workspace",
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
    )
    project = config.workspace / "nested"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.0.1'\n", encoding="utf-8"
    )
    return config


def provider() -> ProviderConfig:
    return ProviderConfig(
        name="lmstudio",
        model="offline-test",
        api_key="test",
        base_url="http://127.0.0.1:9/v1",
    )


def orphan(config: AppConfig):
    with TaskStateStore(config.context_database) as store:
        task = store.create(
            "selected",
            config.workspace,
            "Implement /workspace/nested",
            route_chat_request("Implement /workspace/nested"),
            False,
        )
        claimed = store.claim(task, "lost-owner", 60)
        store.save_checkpoint(claimed, {"next_step": "verify"})
        store.finish(claimed, "blocked", "reconciliation_required: unknown link")
        return store.resolve("selected", config.workspace, task.id)


def source_snapshot(config: AppConfig, task_id: str):
    with TaskStateStore(config.context_database) as store:
        return (
            store.public_by_id(task_id, config.workspace),
            store.checkpoint_by_id(task_id),
        )


def headers(client: TestClient):
    return {"x-csrf-token": client.get("/api/runtime").json()["csrf_token"]}


def no_classifier(*_args, **_kwargs):
    raise AssertionError("Typed actions must not construct or invoke a classifier")


@pytest.mark.parametrize("mode", ["ask", "plan"])
def test_chat_explicit_verification_preserves_mode_boundary(
    tmp_path, monkeypatch, mode
):
    config = configuration(tmp_path)
    source = orphan(config)
    before = source_snapshot(config, source.id)
    monkeypatch.setattr("context_agent.web.semantic_classifier", no_classifier)
    with TestClient(create_app(config, (provider(),))) as client:
        response = client.post(
            "/api/chat",
            headers=headers(client),
            json={
                "query": "Run verification",
                "thread_id": "selected",
                "mode": mode,
                "explicit_action": "continue_verification",
                "continuation_task_id": source.id,
                "expected_revision": source.revision,
                "allow_write": True,
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "ACTION_NOT_AVAILABLE_IN_MODE"
        assert response.json()["diagnostic_id"]
    assert source_snapshot(config, source.id) == before
    with DiagnosticStore(config.diagnostics_database) as journal:
        rows = journal.list_requests(limit=10)
        assert any(row["error_code"] == "action_not_available_in_mode" for row in rows)


def test_reconcile_is_readonly_and_classifier_independent(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    source = orphan(config)
    before = source_snapshot(config, source.id)
    monkeypatch.setattr("context_agent.web.semantic_classifier", no_classifier)
    with TestClient(create_app(config, (provider(),))) as client:
        response = client.post(
            f"/api/tasks/{source.id}/reconcile",
            headers=headers(client),
            json={"thread_id": "selected", "expected_revision": source.revision},
        )
        assert response.status_code == 200, response.text
        assert response.json()["item"]
        assert "create_verification" in json.dumps(response.json())
    assert source_snapshot(config, source.id) == before


@pytest.mark.parametrize("action", ["resume", "reconcile", "verification"])
@pytest.mark.parametrize("invalid", ["thread", "revision"])
def test_explicit_actions_reject_wrong_authority_and_record_diagnostic(
    tmp_path, monkeypatch, action, invalid
):
    config = configuration(tmp_path)
    source = orphan(config)
    before = source_snapshot(config, source.id)
    monkeypatch.setattr("context_agent.web.semantic_classifier", no_classifier)
    body = {
        "thread_id": "selected" if invalid != "thread" else "unrelated",
        "expected_revision": source.revision if invalid != "revision" else 999,
    }
    if action != "reconcile":
        body["idempotency_key"] = "invalid-attempt"
    if action == "verification":
        body["project_root"] = "/workspace/nested"
        body["confirmed"] = True
    with TestClient(create_app(config, (provider(),))) as client:
        response = client.post(
            f"/api/tasks/{source.id}/{action}", headers=headers(client), json=body
        )
        assert response.status_code == 409, response.text
        error = response.json()["error"]
        assert error["code"]
        with DiagnosticStore(config.diagnostics_database) as store:
            record = store.request(error["diagnostic_id"])
            assert record["status"] == "failed"
            assert record["error_code"] == error["code"].lower()
    assert source_snapshot(config, source.id) == before


@pytest.mark.parametrize("action", ["resume", "reconcile", "verification"])
def test_explicit_actions_require_csrf(tmp_path, action):
    config = configuration(tmp_path)
    source = orphan(config)
    with TestClient(create_app(config, (provider(),))) as client:
        response = client.post(
            f"/api/tasks/{source.id}/{action}",
            json={"thread_id": "selected", "expected_revision": source.revision},
        )
        assert response.status_code == 403


@pytest.mark.parametrize("root", ["/workspace/../outside", "/workspace/missing"])
def test_linked_verification_rejects_invalid_project_root(tmp_path, root):
    config = configuration(tmp_path)
    source = orphan(config)
    before = source_snapshot(config, source.id)
    with TestClient(create_app(config, (provider(),))) as client:
        response = client.post(
            f"/api/tasks/{source.id}/verification",
            headers=headers(client),
            json={
                "thread_id": "selected",
                "expected_revision": source.revision,
                "idempotency_key": "invalid-root",
                "confirmed": True,
                "project_root": root,
            },
        )
        assert response.status_code in {409, 422}, response.text
    assert source_snapshot(config, source.id) == before
    with TaskStateStore(config.context_database) as store:
        assert len(store.list("selected", config.workspace)) == 1


@pytest.mark.parametrize("confirmation", [None, False])
def test_linked_verification_requires_explicit_confirmation(tmp_path, confirmation):
    config = configuration(tmp_path)
    source = orphan(config)
    body = {
        "thread_id": "selected",
        "expected_revision": source.revision,
        "project_root": "/workspace/nested",
        "idempotency_key": "verification-no-confirmation",
    }
    if confirmation is not None:
        body["confirmed"] = confirmation
    with TestClient(create_app(config, (provider(),))) as client:
        response = client.post(
            f"/api/tasks/{source.id}/verification", headers=headers(client), json=body
        )
        assert response.status_code == 422
    with TaskStateStore(config.context_database) as store:
        assert len(store.list("selected", config.workspace)) == 1


def test_linked_verification_cannot_smuggle_write_authority(tmp_path):
    config = configuration(tmp_path)
    source = orphan(config)
    with TestClient(create_app(config, (provider(),))) as client:
        response = client.post(
            f"/api/tasks/{source.id}/verification",
            headers=headers(client),
            json={
                "thread_id": "selected",
                "expected_revision": source.revision,
                "project_root": "/workspace/nested",
                "idempotency_key": "verification-no-authority",
                "confirmed": True,
                "allow_write": True,
            },
        )
        assert response.status_code == 422
    with TaskStateStore(config.context_database) as store:
        assert len(store.list("selected", config.workspace)) == 1


def test_linked_verification_replays_same_execution_after_restart(
    tmp_path, monkeypatch
):
    config = configuration(tmp_path)
    source = orphan(config)
    before = source_snapshot(config, source.id)
    calls = []

    class Runtime:
        _provider_failover_middleware = SimpleNamespace(
            runtime_metadata=lambda: {"provider": "lmstudio"}
        )
        context_store = SimpleNamespace(archive_message=lambda *_args: None)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def ask(self, *_args, **_kwargs):
            raise AssertionError("Linked verification must use persistent VERIFY")

        def run_autopilot_job(self, objective, **kwargs):
            calls.append((objective, kwargs))
            # A stub must settle the queued job like a real executor; leaving
            # it queued would intentionally trigger startup queue recovery.
            with AutopilotStore(config.autopilot_database) as store:
                job = store.list_jobs(workspace=config.workspace)[0]
                store.set_control_status(job["id"], "cancelled")
            return "Fixture executor finished; no real project PASS claimed."

    monkeypatch.setattr("context_agent.web.semantic_classifier", no_classifier)
    monkeypatch.setattr(
        "context_agent.web._runtime_factory", lambda *_a, **_k: Runtime()
    )
    body = {
        "thread_id": "selected",
        "expected_revision": source.revision,
        "idempotency_key": "one-verification",
        "confirmed": True,
        "project_root": "/workspace/nested",
    }
    path = f"/api/tasks/{source.id}/verification"
    with TestClient(create_app(config, (provider(),))) as client:
        auth = headers(client)
        first = client.post(path, json=body, headers=auth)
        assert first.status_code == 202, first.text
        payload = first.json()
        client.get(f"/api/events/{payload['task_id']}")
        repeated = client.post(path, json=body, headers=auth)
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["task_id"] == payload["task_id"]
        assert repeated.json()["reused"] is True
        with TaskStateStore(config.context_database) as store:
            child = store.public_by_id(payload["active_task_id"], config.workspace)
        assert child["allow_write"] is False
        assert child["workflow"] == "verification-only"
        assert source_snapshot(config, source.id) == before
    with TestClient(create_app(config, (provider(),))) as client:
        restarted = client.post(path, json=body, headers=headers(client))
        assert restarted.status_code == 202, restarted.text
        assert restarted.json()["task_id"] == payload["task_id"]
        assert restarted.json()["job_id"] == payload["job_id"]
        assert restarted.json()["reused"] is True
    assert len(calls) == 1
    assert calls[0][1]["workflow"] == "verification-only"
    assert calls[0][1]["allow_write"] is False
    assert "/workspace/nested" in calls[0][0]
    assert source_snapshot(config, source.id) == before
    with AutopilotStore(config.autopilot_database) as store:
        assert len(store.list_jobs(workspace=config.workspace)) == 1
