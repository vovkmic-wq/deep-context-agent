"""Security and API tests for the optional local web interface."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from context_agent.config import AppConfig, ProviderConfig
from context_agent.project_audit import ProjectAuditStore
from context_agent.web import create_app


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
