"""Authorized task continuity, isolation and current-mode regressions."""

# ruff: noqa: RUF001 -- Russian direct instructions are regression fixtures

import pytest
from conftest import SequenceChatModel
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from context_agent.config import AppConfig, ProviderConfig
from context_agent.context_store import ContextStore
from context_agent.routing import route_chat_request
from context_agent.runtime import AgentRuntime
from context_agent.task_state import (
    TaskConflict,
    TaskStateStore,
    is_continuation,
    resume_route,
)
from context_agent.web import create_app


def tool(name, args, call_id):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def create_task(store, tmp_path, thread="main"):
    query = "Исправь код проекта и проведи тесты."
    return store.create(thread, tmp_path, query, route_chat_request(query), True)


def test_continuation_is_direct_instruction_only():
    assert is_continuation("продолжи выполнение задачи")
    assert is_continuation("выполни следующий пункт")
    assert not is_continuation("Проанализируй лог:\nпродолжи")
    assert not is_continuation("```\nпродолжи\n```")
    assert not is_continuation("есть ли невыполненные задачи?")


def test_store_survives_restart_and_preserves_context_database(tmp_path):
    database = tmp_path / "context.sqlite3"
    with ContextStore(database) as archive:
        archive.archive_message("main", "user", "sentinel")
    with TaskStateStore(database) as store:
        task = create_task(store, tmp_path)
        task = store.claim(task, "owner", 900)
        assert store.finish(task, "partial", "request:1; verified_write:1")
    with TaskStateStore(database) as store:
        resumed = store.resolve("main", tmp_path)
        assert resumed.id == task.id
        assert resumed.revision == 3
        assert resumed.evidence == "request:1; verified_write:1"
        with pytest.raises(TaskConflict):
            store.resolve("other", tmp_path)
        with pytest.raises(TaskConflict):
            store.resolve("main", tmp_path / "other-workspace")
    with ContextStore(database) as archive:
        assert archive.search("sentinel")


def test_ambiguous_cancelled_completed_and_stale_tasks(tmp_path):
    with TaskStateStore(tmp_path / "tasks.sqlite3") as store:
        first = create_task(store, tmp_path)
        create_task(store, tmp_path)
        with pytest.raises(TaskConflict):
            store.resolve("main", tmp_path)
        claimed = store.claim(store.resolve("main", tmp_path, first.id), "owner", 50)
        assert store.owns(claimed)
        with pytest.raises(TaskConflict):
            store.claim(first, "other", 50)
        assert store.finish(claimed, "cancelled", "operator_cancelled")
        assert not store.finish(claimed, "completed", "stale")
        assert not store.owns(claimed)
        with pytest.raises(TaskConflict):
            store.resolve("main", tmp_path, first.id)


def test_resume_intersects_saved_authority_and_current_mode(tmp_path):
    with TaskStateStore(tmp_path / "tasks.sqlite3") as store:
        task = create_task(store, tmp_path)
    agent = resume_route(task, "продолжи", "agent", "auto")
    ask = resume_route(task, "продолжи", "ask", "auto")
    assert agent.scope == ask.scope == "project"
    assert agent.mutation_requested
    assert not ask.mutation_requested
    assert ask.execution == "single-turn"


def test_busy_new_request_does_not_leave_ambiguous_orphan(tmp_path):
    database = tmp_path / "context.sqlite3"
    with TaskStateStore(database) as first, TaskStateStore(database) as second:
        task = first.claim(create_task(first, tmp_path), "owner", 900)
        with pytest.raises(TaskConflict):
            second.prepare_turn(
                query="Измени код проекта.",
                thread="main",
                workspace=tmp_path,
                mode="agent",
                execution="auto",
                allow_write=True,
                owner="second",
                lease_seconds=900,
            )
        assert len(second.list("main", tmp_path)) == 1
        first.close_task(task.id, "main", tmp_path, task.revision, "cancelled")
        assert not second.owns(task)
        with pytest.raises(TaskConflict):
            second.close_task(task.id, "main", tmp_path, task.revision, "completed")


def test_expired_lease_can_be_claimed_but_old_owner_cannot_finish(tmp_path):
    with TaskStateStore(tmp_path / "context.sqlite3") as store:
        stale = store.claim(create_task(store, tmp_path), "dead", -1)
        current = store.claim(store.resolve("main", tmp_path), "new", 900)
        assert store.owns(current)
        assert not store.owns(stale)
        assert not store.finish(stale, "completed", "stale")


def test_cli_user_boundary_restores_task_and_honors_revoked_write(tmp_path):
    config = AppConfig(
        project_root=tmp_path,
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        context_root=tmp_path / "workspace",
    )
    provider = ProviderConfig(
        name="lmstudio",
        model="fake",
        api_key="test",
        base_url="http://localhost:1234/v1",
    )
    model = SequenceChatModel(
        responses=[
            tool(
                "write_file",
                {"file_path": "/workspace/task.txt", "content": "FIRST"},
                "c1",
            ),
            AIMessage(content="Stage one"),
            AIMessage(content="Log examined"),
            tool(
                "edit_file",
                {
                    "file_path": "/workspace/task.txt",
                    "old_string": "FIRST",
                    "new_string": "BAD",
                },
                "c2",
            ),
            AIMessage(content="No write permission"),
        ]
    )
    with AgentRuntime(config, provider, model=model) as runtime:
        runtime.ask_user(
            "Создай /workspace/task.txt с текстом FIRST.",
            thread_id="cli",
            auto_context=False,
        )
        runtime.ask_user(
            "Проанализируй лог:\nWorkspace access denied",
            thread_id="cli",
            auto_context=False,
        )
        runtime.ask_user(
            "продолжи", thread_id="cli", auto_context=False, allow_write=False
        )
    assert (config.workspace / "task.txt").read_text() == "FIRST"
    with TaskStateStore(config.context_database) as store:
        assert len(store.list("cli", config.workspace)) == 1


def test_web_develop_log_resume_and_ask_with_real_runtime(tmp_path, monkeypatch):
    config = AppConfig(
        project_root=tmp_path,
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        context_root=tmp_path / "workspace",
    )
    provider = ProviderConfig(
        name="lmstudio",
        model="fake",
        api_key="test",
        base_url="http://localhost:1234/v1",
    )
    models = iter(
        [
            SequenceChatModel(
                responses=[
                    tool(
                        "write_file",
                        {"file_path": "/workspace/task.txt", "content": "FIRST"},
                        "w1",
                    ),
                    AIMessage(content="First stage done; next stage pending."),
                ]
            ),
            SequenceChatModel(responses=[AIMessage(content="This is an old denial.")]),
            SequenceChatModel(
                responses=[
                    tool("read_file", {"file_path": "/workspace/task.txt"}, "r2"),
                    tool(
                        "edit_file",
                        {
                            "file_path": "/workspace/task.txt",
                            "old_string": "FIRST",
                            "new_string": "FIRST\nSECOND",
                        },
                        "w2",
                    ),
                    AIMessage(content="Second stage done."),
                ]
            ),
            SequenceChatModel(
                responses=[
                    tool(
                        "edit_file",
                        {
                            "file_path": "/workspace/task.txt",
                            "old_string": "SECOND",
                            "new_string": "UNAUTHORIZED",
                        },
                        "w3",
                    ),
                    AIMessage(content="Write denied in Ask."),
                ]
            ),
        ]
    )
    seen_models = []

    def factory(app_config, _providers):
        model = next(models)
        seen_models.append(model)
        return AgentRuntime(app_config, provider, model=model)

    monkeypatch.setattr("context_agent.web._runtime_factory", factory)
    with TestClient(create_app(config, (provider,))) as client:
        csrf = client.get("/api/runtime").json()["csrf_token"]

        def submit(query, mode="agent", allow_write=True):
            response = client.post(
                "/api/chat",
                headers={"x-csrf-token": csrf},
                json={
                    "query": query,
                    "thread_id": "main",
                    "mode": mode,
                    "allow_write": allow_write,
                    "execution_mode": "single-turn",
                    "auto_context": False,
                },
            )
            assert response.status_code == 202, response.text
            task_id = response.json()["task_id"]
            stream = client.get(f"/api/events/{task_id}").text
            assert "event: failed" not in stream, stream
            return response.json(), stream

        first, stream = submit("Создай /workspace/task.txt с текстом FIRST.")
        assert "event: partial" in stream
        assert (config.workspace / "task.txt").read_text() == "FIRST"
        log, stream = submit(
            "Проанализируй лог:\nWorkspace access denied\nИсправь весь проект"
        )
        assert log["routing"]["scope"] == "message"
        assert log["active_task"] is None
        assert "event: completed" in stream
        resumed, stream = submit("продолжи выполнение задачи")
        assert resumed["active_task"]["task_id"] == first["active_task"]["task_id"]
        assert resumed["routing"]["scope"] == "file"
        assert (config.workspace / "task.txt").read_text() == "FIRST\nSECOND"
        policy = seen_models[2].received_message_batches[0][0].text
        assert '"read_file": true' in policy
        assert "<trusted_turn_policy>" in policy
        submit("продолжи", mode="ask")
        assert (config.workspace / "task.txt").read_text() == "FIRST\nSECOND"
        tasks = client.get("/api/threads/main/active-tasks").json()["items"]
        assert len(tasks) == 1
        assert str(tmp_path) not in str(tasks)
        url = f"/api/threads/main/active-tasks/{tasks[0]['task_id']}"
        closed = client.patch(
            url,
            headers={"x-csrf-token": csrf},
            json={"status": "completed", "revision": tasks[0]["revision"]},
        )
        assert closed.status_code == 200
        stale = client.patch(
            url,
            headers={"x-csrf-token": csrf},
            json={"status": "cancelled", "revision": tasks[0]["revision"]},
        )
        assert stale.status_code == 409
        denied = client.post(
            "/api/chat",
            headers={"x-csrf-token": csrf},
            json={"query": "продолжи", "thread_id": "main"},
        )
        assert denied.status_code == 409
        assert len(seen_models) == 4
