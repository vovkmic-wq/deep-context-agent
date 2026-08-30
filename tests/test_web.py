"""Security and API tests for the optional local web interface."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from context_agent.config import AppConfig, ProviderConfig
from context_agent.diagnostics import DiagnosticStore
from context_agent.errors import AgentError
from context_agent.project_audit import ProjectAuditStore
from context_agent.web import (
    _agent_failure_code,
    _is_benign_windows_pipe_reset,
    create_app,
)


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        project_root=tmp_path,
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        context_root=tmp_path / "workspace",
    )


def _provider() -> ProviderConfig:
    return ProviderConfig(
        name="lmstudio",
        model="test-model",
        base_url="http://127.0.0.1:1234/v1",
        api_key="test-key",
    )


def _client(tmp_path: Path) -> tuple[TestClient, str, AppConfig]:
    config = _config(tmp_path)
    app = create_app(config, (_provider(),))
    client = TestClient(app)
    response = client.get("/api/runtime")
    assert response.status_code == 200
    return client, str(response.json()["csrf_token"]), config


def test_web_health_static_bundle_and_security_headers(tmp_path: Path) -> None:
    client, _csrf, _config_value = _client(tmp_path)
    with client:
        health = client.get("/api/health")
        page = client.get("/")
    assert health.json()["status"] == "ok"
    assert health.json()["request_id"]
    assert "default-src 'self'" in health.headers["content-security-policy"]
    assert page.status_code == 200
    assert "Deep Context Agent" in page.text


def test_web_mutations_require_csrf_and_reject_foreign_origin(tmp_path: Path) -> None:
    client, csrf, _config_value = _client(tmp_path)
    body = {"thread_id": "safe-thread"}
    assert client.post("/api/threads", json=body).status_code == 403
    denied = client.post(
        "/api/threads",
        json=body,
        headers={"x-csrf-token": csrf, "origin": "https://evil.invalid"},
    )
    accepted = client.post(
        "/api/threads",
        json=body,
        headers={"x-csrf-token": csrf},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "origin_denied"
    assert accepted.status_code == 200


def test_files_api_blocks_traversal_secrets_and_stale_writes(tmp_path: Path) -> None:
    client, csrf, config = _client(tmp_path)
    target = config.workspace / "note.txt"
    target.write_text("first\n", encoding="utf-8")
    (config.workspace / ".env").write_text("SECRET=value\n", encoding="utf-8")
    headers = {"x-csrf-token": csrf}

    read = client.get("/api/files/note.txt")
    stale = client.put(
        "/api/files/note.txt",
        json={"content": "second\n", "expected_sha256": "0" * 64},
        headers=headers,
    )
    saved = client.put(
        "/api/files/note.txt",
        json={
            "content": "second\n",
            "expected_sha256": read.json()["sha256"],
        },
        headers=headers,
    )
    secret = client.get("/api/files/.env")
    traversal = client.get("/api/files", params={"path": "../../"})
    workspace_root = client.get("/api/files", params={"path": "/workspace"})

    assert read.json()["content"] == "first\n"
    assert stale.status_code == 409
    assert saved.status_code == 200
    assert target.read_text(encoding="utf-8") == "second\n"
    assert secret.status_code == 404
    assert traversal.status_code == 404
    assert workspace_root.status_code == 200
    assert workspace_root.json()["items"][0]["path"] == "/workspace/note.txt"


def test_web_never_returns_provider_key_and_delete_is_off(tmp_path: Path) -> None:
    client, csrf, config = _client(tmp_path)
    target = config.workspace / "delete-me.txt"
    target.write_text("keep\n", encoding="utf-8")
    providers = client.get("/api/providers")
    deletion = client.request(
        "DELETE",
        "/api/files/delete-me.txt",
        json={"confirm_path": "/workspace/delete-me.txt"},
        headers={"x-csrf-token": csrf},
    )
    serialized = providers.text
    assert "test-key" not in serialized
    assert providers.json()["items"][0]["api_key"] == "configured"
    assert deletion.status_code == 403
    assert target.exists()


def test_provider_priority_changes_atomically_for_live_web_runtime(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    fallback = ProviderConfig(
        name="openai",
        model="fallback-model",
        base_url="https://api.openai.com/v1",
        api_key="fallback-secret",
    )
    app = create_app(config, (_provider(), fallback))
    client = TestClient(app)
    csrf = str(client.get("/api/runtime").json()["csrf_token"])

    changed = client.put(
        "/api/providers/priority",
        json={"providers": ["openai", "lmstudio"]},
        headers={"x-csrf-token": csrf},
    )
    runtime = client.get("/api/runtime")
    catalog = client.get("/api/providers")

    assert changed.status_code == 200
    assert changed.json()["effective_immediately"] is True
    assert runtime.json()["provider_priority"] == ["openai", "lmstudio"]
    assert catalog.json()["active"] == ["openai", "lmstudio"]
    assert "fallback-secret" not in catalog.text


def test_custom_local_provider_can_be_created_and_activated(tmp_path: Path) -> None:
    client, csrf, _config_value = _client(tmp_path)
    created = client.post(
        "/api/providers",
        json={
            "name": "custom-local",
            "model": "local-chat",
            "base_url": "http://127.0.0.1:4321/v1",
        },
        headers={"x-csrf-token": csrf},
    )
    changed = client.put(
        "/api/providers/priority",
        json={"providers": ["custom-local", "lmstudio"]},
        headers={"x-csrf-token": csrf},
    )
    catalog = client.get("/api/providers")

    assert created.status_code == 201
    assert created.json()["provider"]["local"] is True
    assert "local-provider" not in created.text
    assert changed.json()["active"] == ["custom-local", "lmstudio"]
    custom = next(
        item for item in catalog.json()["items"] if item["provider"] == "custom-local"
    )
    assert custom["custom"] is True
    assert custom["active"] is True


def test_custom_provider_rejects_remote_http_and_browser_secret(
    tmp_path: Path,
) -> None:
    client, csrf, _config_value = _client(tmp_path)
    remote_http = client.post(
        "/api/providers",
        json={
            "name": "custom-insecure",
            "model": "remote-chat",
            "base_url": "http://models.example/v1",
        },
        headers={"x-csrf-token": csrf},
    )
    secret_field = client.post(
        "/api/providers",
        json={
            "name": "custom-secret",
            "model": "remote-chat",
            "base_url": "https://models.example/v1",
            "api_key": "must-not-be-accepted",
        },
        headers={"x-csrf-token": csrf},
    )

    assert remote_http.status_code == 422
    assert secret_field.status_code == 422
    assert "must-not-be-accepted" not in secret_field.text


def test_lmstudio_doctor_selects_loaded_chat_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ProviderConfig(
        name="lmstudio",
        model="local-model",
        base_url="http://127.0.0.1:1234/v1",
        api_key="lm-studio",
    )
    monkeypatch.setattr(
        "context_agent.web._probe_openai_models",
        lambda _provider: ("embedding-model", "qwen-local"),
    )

    class _Model:
        def invoke(self, _query: str) -> SimpleNamespace:
            return SimpleNamespace(content="OK")

    monkeypatch.setattr(
        "context_agent.web.create_chat_model", lambda _provider: _Model()
    )
    app = create_app(_config(tmp_path), (provider,))
    client = TestClient(app)
    csrf = str(client.get("/api/runtime").json()["csrf_token"])
    started = client.post(
        "/api/providers/lmstudio/doctor",
        json={"live": True},
        headers={"x-csrf-token": csrf},
    )
    events = client.get(f"/api/events/{started.json()['task_id']}")
    catalog = client.get("/api/providers").json()["items"]
    lmstudio = next(item for item in catalog if item["provider"] == "lmstudio")

    assert started.status_code == 200
    assert '"model": "qwen-local"' in events.text
    assert '"local": true' in events.text
    assert "event: completed" in events.text
    assert lmstudio["model"] == "qwen-local"


def test_expected_agent_error_has_safe_operator_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingRuntime:
        def __enter__(self) -> _FailingRuntime:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ask(self, *_args: object, **_kwargs: object) -> str:
            raise AgentError("private provider detail")

    monkeypatch.setattr(
        "context_agent.web._runtime_factory",
        lambda *_args, **_kwargs: _FailingRuntime(),
    )
    client, csrf, _config_value = _client(tmp_path)
    started = client.post(
        "/api/chat",
        json={"query": "test", "thread_id": "safe-error"},
        headers={"x-csrf-token": csrf},
    )
    events = client.get(f"/api/events/{started.json()['task_id']}")

    assert "Проверьте их live-статус" in events.text
    assert "private provider detail" not in events.text


def test_failed_task_keeps_sanitized_terminal_status_and_replays_sse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingRuntime:
        def __enter__(self) -> _FailingRuntime:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ask(self, *_args: object, **_kwargs: object) -> str:
            raise AgentError("maximum context length exceeded; PRIVATE_PROVIDER_DETAIL")

    monkeypatch.setattr(
        "context_agent.web._runtime_factory",
        lambda *_args, **_kwargs: _FailingRuntime(),
    )
    client, csrf, _config_value = _client(tmp_path)
    started = client.post(
        "/api/chat",
        json={"query": "test", "thread_id": "terminal-error"},
        headers={"x-csrf-token": csrf},
    )
    task_id = started.json()["task_id"]
    first_stream = client.get(f"/api/events/{task_id}")
    status = client.get(f"/api/tasks/{task_id}")
    replay = client.get(f"/api/events/{task_id}")

    assert '"error_type": "context_window_exceeded"' in first_stream.text
    assert status.json()["status"] == "failed"
    assert status.json()["terminal"]["error_type"] == "context_window_exceeded"
    assert "event: failed" in replay.text
    assert "PRIVATE_PROVIDER_DETAIL" not in first_stream.text + replay.text


def test_terminal_task_replays_after_web_process_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingRuntime:
        def __enter__(self) -> _FailingRuntime:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ask(self, *_args: object, **_kwargs: object) -> str:
            raise AgentError("provider timeout PRIVATE_RESTART_DETAIL")

    monkeypatch.setattr(
        "context_agent.web._runtime_factory",
        lambda *_args, **_kwargs: _FailingRuntime(),
    )
    config = _config(tmp_path)
    first_app = create_app(config, (_provider(),))
    with TestClient(first_app) as first:
        csrf = str(first.get("/api/runtime").json()["csrf_token"])
        started = first.post(
            "/api/chat",
            json={"query": "restart", "thread_id": "restart-failure"},
            headers={"x-csrf-token": csrf},
        )
        task_id = str(started.json()["task_id"])
        assert "event: failed" in first.get(f"/api/events/{task_id}").text

    second_app = create_app(config, (_provider(),))
    with TestClient(second_app) as second:
        status = second.get(f"/api/tasks/{task_id}")
        replay = second.get(f"/api/events/{task_id}")

    assert status.status_code == 200
    assert status.json()["status"] == "failed"
    assert status.json()["terminal"]["request_id"] == task_id
    assert status.json()["terminal"]["retryable"] is True
    assert "event: failed" in replay.text
    assert "PRIVATE_RESTART_DETAIL" not in replay.text


def test_diagnostic_api_hides_query_by_default_and_purges_explicit_id(
    tmp_path: Path,
) -> None:
    fixture_key = "sk-" + "proj-" + "hidden-fixture-58213"
    config = _config(tmp_path)
    config.prepare_directories()
    with DiagnosticStore(config.diagnostics_database) as store:
        request_id = store.start_request(
            query=f"FAILED_API_58213 OPENAI_API_KEY={fixture_key}",
            thread_id="api-failure",
            operation_kind="ask",
            source="web",
            app_version="test",
            provider_priority=[],
            baseline_checkpoint_id=None,
            request_id="diagnostic-api-request",
        )
        assert request_id is not None
        store.fail_request(
            request_id,
            exc=TimeoutError(f"timeout {fixture_key}"),
            provider_attempts=[],
            tool_audit=[],
            duration_ms=1,
            rollback_attempted=True,
            rollback_success=True,
            rollback_checkpoint_rows=1,
            rollback_write_rows=1,
            filesystem_side_effects=False,
        )

    client, csrf, _config_value = _client(tmp_path)
    listing = client.get("/api/diagnostics")
    details = client.get("/api/diagnostics/diagnostic-api-request")
    disclosed = client.get("/api/diagnostics/diagnostic-api-request?include_query=true")
    purged = client.request(
        "DELETE",
        "/api/diagnostics",
        json={"confirm": "PURGE", "request_id": "diagnostic-api-request"},
        headers={"x-csrf-token": csrf},
    )

    combined = listing.text + details.text + disclosed.text
    assert listing.status_code == 200
    assert "query" not in details.json()["item"]
    assert "FAILED_API_58213" in disclosed.json()["item"]["query"]
    assert "hidden-fixture-58213" not in combined
    assert purged.json()["deleted"] == 1


def test_structured_web_log_contains_safe_correlation_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingRuntime:
        def __enter__(self) -> _FailingRuntime:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ask(self, *_args: object, **_kwargs: object) -> str:
            raise AgentError("SECRET_LOG_DETAIL_75291")

    monkeypatch.setattr(
        "context_agent.web._runtime_factory",
        lambda *_args, **_kwargs: _FailingRuntime(),
    )
    client, csrf, config = _client(tmp_path)
    started = client.post(
        "/api/chat",
        json={"query": "log", "thread_id": "structured-log"},
        headers={"x-csrf-token": csrf},
    )
    task_id = str(started.json()["task_id"])
    client.get(f"/api/events/{task_id}")
    log = (config.data_dir / "context-agent-server.jsonl").read_text(encoding="utf-8")

    record = json.loads(log.splitlines()[-1])
    assert record["event_code"] == "provider_chain_failed"
    assert record["fields"]["task_id"] == task_id
    assert "SECRET_LOG_DETAIL_75291" not in log


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AgentError("credit_balance_exhausted"), "quota_exhausted"),
        (AgentError("RateLimitError: rate limit"), "rate_limited"),
        (AgentError("GraphRecursionError"), "agent_step_limit"),
        (AgentError("unexpected provider failure"), "provider_chain_failed"),
    ],
)
def test_agent_failure_classification_is_stable(
    error: AgentError,
    code: str,
) -> None:
    assert _agent_failure_code(error) == code


def test_only_known_windows_proactor_reset_is_benign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset = ConnectionResetError("closed")
    monkeypatch.setattr(reset, "winerror", 10054, raising=False)
    monkeypatch.setattr("context_agent.web.os.name", "nt")
    known = {
        "exception": reset,
        "message": (
            "Exception in callback "
            "_ProactorBasePipeTransport._call_connection_lost(None)"
        ),
    }

    assert _is_benign_windows_pipe_reset(known)
    assert not _is_benign_windows_pipe_reset(
        {"exception": reset, "message": "unrelated network operation"}
    )


def test_context_index_uses_virtual_workspace_root_and_reports_result(
    tmp_path: Path,
) -> None:
    client, csrf, config = _client(tmp_path)
    (config.workspace / "document.txt").write_text("INDEX_MARKER\n", encoding="utf-8")
    started = client.post(
        "/api/context/index",
        json={"path": "/workspace"},
        headers={"x-csrf-token": csrf},
    )
    events = client.get(f"/api/events/{started.json()['task_id']}")

    assert started.status_code == 202
    assert events.status_code == 200
    assert '"files_indexed": 1' in events.text
    assert "event: completed" in events.text


def test_settings_and_work_modes_include_russian_explanations(tmp_path: Path) -> None:
    client, csrf, _config_value = _client(tmp_path)
    settings = client.get("/api/settings").json()
    runtime = client.get("/api/runtime").json()
    invalid = client.put(
        "/api/settings",
        json={"values": {"audit_batch_size": 26}},
        headers={"x-csrf-token": csrf},
    )

    assert settings["items"]
    assert all(item["comment"] for item in settings["items"])
    assert all(" / " in item["label"] for item in settings["items"])
    assert {"audit", "coder", "tester", "security"}.issubset(runtime["work_modes"])
    assert invalid.status_code == 422


def test_remote_web_mode_requires_authentication_token(tmp_path: Path) -> None:
    config = _config(tmp_path)
    try:
        create_app(config, (_provider(),), allow_remote=True)
    except ValueError as error:
        assert "AGENT_WEB_AUTH_TOKEN" in str(error)
    else:
        raise AssertionError("Remote mode must require an authentication token")


def test_audit_status_report_and_control_share_cli_database(tmp_path: Path) -> None:
    client, csrf, config = _client(tmp_path)
    (config.workspace / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    with ProjectAuditStore(config.project_audit_database) as store:
        progress = store.start_or_resume(
            thread_id="web-status",
            objective="Read-only audit",
            workspace=config.workspace,
            batch_size=1,
        )
        run_id = progress.run_id

    details = client.get(f"/api/audits/{run_id}")
    report = client.get(f"/api/audits/{run_id}/report?format=json")
    paused = client.post(
        f"/api/audits/{run_id}/pause",
        headers={"x-csrf-token": csrf},
    )

    assert details.json()["audit"]["mode"] == "read-only"
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("application/json")
    assert report.json()["run"]["id"] == run_id
    assert paused.json()["progress"]["status"] == "paused"


def test_web_error_envelope_hides_real_paths_and_tracebacks(tmp_path: Path) -> None:
    client, _csrf, config = _client(tmp_path)
    response = client.get("/api/files", params={"path": "../../secret"})
    serialized = response.text
    assert response.status_code == 404
    assert response.json()["error"]["request_id"]
    assert str(config.workspace) not in serialized
    assert "Traceback" not in serialized
