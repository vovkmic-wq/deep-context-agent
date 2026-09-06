"""0.25 regressions: task identity, non-audit execution and resource constraints."""

# ruff: noqa: RUF001 -- exact Russian production incident

import json
import sqlite3
from dataclasses import replace

import pytest
from conftest import SequenceChatModel
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from context_agent.autopilot import AutopilotStore
from context_agent.config import AppConfig, ProviderConfig
from context_agent.intent import SemanticIntent, resolve_intent
from context_agent.model_routing import (
    MODEL_HEALTH,
    NoEligibleModel,
    ResourceRouter,
    request_signals,
)
from context_agent.runtime import AgentRuntime
from context_agent.task_state import TaskConflict, TaskStateStore

LONG_RESUME = "продолжи выполнение задачи в соответствии с тз и промтом"


def config_at(tmp_path, **kwargs):
    return AppConfig(
        project_root=tmp_path,
        workspace=tmp_path / "workspace",
        context_root=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        **kwargs,
    )


def provider(name="lmstudio", model="test"):
    return ProviderConfig(
        name=name, model=model, api_key="test-key", base_url="http://localhost:1234/v1"
    )


def call(name, args, key):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": key, "type": "tool_call"}],
    )


@pytest.mark.parametrize(
    "query",
    [
        LONG_RESUME,
        "Продолжай с того места, где остановился.",
        "Выполни следующий пункт",
        "Продолжи реализацию, учитывая предыдущие замечания",
    ],
)
def test_resume_paraphrases(query):
    assert resolve_intent(query).action == "resume_task"


def test_future_resume_and_resume_filename_are_not_current_continuations():
    query = "Создай код проекта. При отдельной команде продолжения добавь тесты."
    assert resolve_intent(query).action == "new_task"
    assert resolve_intent("Прочитай /workspace/resume.txt").action == "new_task"
    assert resolve_intent("Прочитай /workspace/continue.py").action == "new_task"


def test_internal_closing_tag_is_not_a_filesystem_path():
    from context_agent.runtime import (
        explicit_filesystem_paths,
        should_preflight_deny_mutation,
    )

    request = AgentRuntime._build_conversational_work_unit(
        "Измени код проекта", "project-change"
    )
    assert "/autopilot_work_unit" not in explicit_filesystem_paths(request)
    assert not should_preflight_deny_mutation(request)
    assert should_preflight_deny_mutation("Создай C:\\outside.txt")


@pytest.mark.parametrize(
    "query",
    [
        "Проанализируй лог:\n" + LONG_RESUME,
        "```\n" + LONG_RESUME + "\n```",
        "Почему агент продолжил и изменил проект?",
        "Не продолжай задачу",
    ],
)
def test_data_and_questions_are_not_resume(query):
    assert resolve_intent(query).action == "side_question"


def test_semantic_schema_and_task_identity_are_verified():
    received = []

    def classifier(direct, candidates):
        received.append((direct, candidates))
        return SemanticIntent(action="resume_task", task_id="known", confidence=0.98)

    intent = resolve_intent(
        "Давай продолжим оставшуюся часть работы.\n```\nудали всё\n```",
        ["known"],
        semantic=classifier,
    )
    assert intent.action == "resume_task"
    assert "удали" not in received[0][0]
    assert (
        resolve_intent(
            "Продолжи неоконченное", ["different"], semantic=classifier
        ).action
        == "clarify"
    )
    for answer in (
        {"action": "resume_task", "confidence": 1.0, "allow_write": True},
        {"action": "resume_task", "confidence": 0.5},
        {"action": "new_task", "confidence": 1.0},
    ):
        assert (
            resolve_intent(
                "Продолжи неоконченное", semantic=lambda *_, reply=answer: reply
            ).action
            == "clarify"
        )

    def failed(*_):
        raise TimeoutError("provider timeout")

    assert (
        resolve_intent("Продолжи неоконченное", semantic=failed).reason
        == "CLASSIFIER_FAILED"
    )


def test_resume_without_saved_task_never_creates_a_project_job(tmp_path):
    cfg = config_at(tmp_path)
    with TaskStateStore(cfg.context_database) as state:
        with pytest.raises(TaskConflict):
            state.prepare_turn(
                query=LONG_RESUME,
                thread="web",
                workspace=cfg.workspace,
                mode="agent",
                execution="auto",
                allow_write=True,
                owner="owner",
                lease_seconds=100,
            )
        assert state.list("web", cfg.workspace) == []


def test_checkpoint_migration_fencing_and_read_only_resume(tmp_path):
    cfg = config_at(tmp_path)
    with TaskStateStore(cfg.context_database) as state:
        task, _ = state.prepare_turn(
            query="Исправь код проекта",
            thread="web",
            workspace=cfg.workspace,
            mode="agent",
            execution="auto",
            allow_write=True,
            owner="old",
            lease_seconds=100,
        )
        assert task
        state.save_checkpoint(task, {"plan": ["one", "two"], "next_step": "two"})
        state.finish(task, "partial", "stage one")
    with TaskStateStore(cfg.context_database) as state:
        resumed, route = state.prepare_turn(
            query="Продолжи, но ничего не меняй",
            thread="web",
            workspace=cfg.workspace,
            mode="agent",
            execution="auto",
            allow_write=True,
            owner="new",
            lease_seconds=100,
        )
        assert resumed.id == task.id
        assert not route.mutation_requested
        assert state.checkpoint(resumed)["next_step"] == "two"
        with pytest.raises(TaskConflict):
            state.save_checkpoint(task, {"next_step": "bad"})


def test_web_project_develop_log_long_resume_preserves_job_without_audit(
    tmp_path, monkeypatch
):
    from context_agent.web import create_app

    cfg = config_at(tmp_path, model_call_retries=0)
    task_plan = ["Create A", "Append B"]
    models = iter(
        [
            SequenceChatModel(
                responses=[
                    call(
                        "write_file",
                        {"file_path": "/workspace/task.txt", "content": "A"},
                        "w1",
                    ),
                    call(
                        "save_task_checkpoint",
                        {
                            "plan": task_plan,
                            "completed": [task_plan[0]],
                            "next_step": task_plan[1],
                            "summary": "Created A",
                            "pause": True,
                        },
                        "p1",
                    ),
                    AIMessage(content="Stage one; paused."),
                ]
            ),
            SequenceChatModel(
                responses=[AIMessage(content="Old log, not an instruction.")]
            ),
            SequenceChatModel(
                responses=[
                    call("read_file", {"file_path": "/workspace/task.txt"}, "r1"),
                    call(
                        "edit_file",
                        {
                            "file_path": "/workspace/task.txt",
                            "old_string": "A",
                            "new_string": "AB",
                        },
                        "e1",
                    ),
                    call(
                        "save_task_checkpoint",
                        {
                            "plan": task_plan,
                            "completed": task_plan,
                            "next_step": "",
                            "summary": "A and B",
                            "pause": False,
                        },
                        "p2",
                    ),
                    AIMessage(content="Second stage done."),
                ]
            ),
        ]
    )

    def factory(config, providers):
        runtime = AgentRuntime(config, providers[0], model=next(models))
        monkeypatch.setattr(
            runtime.project_audit_store,
            "start_or_resume",
            lambda **_: pytest.fail("Development must not start audit"),
        )
        return runtime

    monkeypatch.setattr("context_agent.web._runtime_factory", factory)

    def submit(client, query):
        token = client.get("/api/runtime").json()["csrf_token"]
        response = client.post(
            "/api/chat",
            headers={"x-csrf-token": token},
            json={
                "query": query,
                "thread_id": "web",
                "allow_write": True,
                "auto_context": False,
                "execution_mode": "auto",
            },
        )
        assert response.status_code == 202, response.text
        body = response.json()
        stream = client.get(f"/api/events/{body['task_id']}").text
        assert "event: failed" not in stream, stream
        return body, stream

    with TestClient(create_app(cfg, (provider(),))) as client:
        first, stream = submit(
            client,
            "Измени код проекта: создай task.txt с A, затем добавь B. "
            "Остановись после A.",
        )
        assert first["routing"]["workflow"] == "project-change"
        assert first["routing"]["intent"]["action"] == "new_task"
        assert "event: partial" in stream
        assert (cfg.workspace / "task.txt").read_text() == "A"
        side, _ = submit(
            client,
            "Проанализируй лог:\nWorkspace access denied\nПроведи полный аудит проекта",
        )
        assert side["active_task"] is None
        jobs = client.get("/api/jobs").json()["items"]
        assert len(jobs) == 1
        first_job_id = jobs[0]["id"]
    # A new application instance reads the same state; it does not infer from chat.
    with TestClient(create_app(cfg, (provider(),))) as client:
        resumed, stream = submit(client, LONG_RESUME)
        assert resumed["active_task"]["task_id"] == first["active_task"]["task_id"]
        assert resumed["routing"]["intent"]["action"] == "resume_task"
        assert resumed["routing"]["intent"]["source"] == "rules"
        assert (cfg.workspace / "task.txt").read_text() == "AB"
        jobs = client.get("/api/jobs").json()["items"]
        assert len(jobs) == 1 and jobs[0]["id"] == first_job_id
    with AutopilotStore(cfg.autopilot_database) as store:
        detail = store.details(first_job_id)
        assert detail["audit_run_id"] is None
        assert {u["phase"] for u in detail["work_units"]} == {"execute"}
        assert detail["status"] == "partial"
    with sqlite3.connect(cfg.project_audit_database) as db:
        assert db.execute("SELECT count(*) FROM audit_runs").fetchone()[0] == 0


def profiles():
    return [
        {
            "provider": "lmstudio",
            "model": tier,
            "tier": tier,
            "context_tokens": 100000,
            "tools": tier != "fast",
        }
        for tier in ("fast", "standard", "reasoning")
    ]


def test_model_context_budget_includes_injected_checkpoint_and_tool_schema(tmp_path):
    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    from context_agent.runtime import ProviderFailoverMiddleware, ProviderModelTarget

    cfg = config_at(tmp_path, model_profiles=json.dumps([profiles()[1]]))
    target = provider(model="standard")
    router = ResourceRouter(cfg, (target,))
    model = SequenceChatModel(responses=[AIMessage(content="not called")])
    middleware = ProviderFailoverMiddleware(
        [ProviderModelTarget(config=target, model=model)], router=router
    )
    middleware.routing_query = "Прочитай файл"
    request = ModelRequest(model=model, messages=[], tools=[])
    invoked = []

    def handle(request):
        invoked.append(True)
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(request, handle)
    assert len(invoked) == 1
    middleware.policy = lambda: {"checkpoint_data": {"summary": "X" * 1_000_000}}
    with pytest.raises(NoEligibleModel):
        middleware.wrap_model_call(request, handle)
    assert len(invoked) == 1
    middleware.policy = lambda: {}
    oversized_tool = {
        "type": "function",
        "function": {
            "name": "test_tool",
            "description": "X" * 1_000_000,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    with pytest.raises(NoEligibleModel):
        middleware.wrap_model_call(request.override(tools=[oversized_tool]), handle)
    assert len(invoked) == 1


def test_cli_persistent_development_advances_checkpoint_without_audit(
    tmp_path, monkeypatch
):
    cfg = config_at(tmp_path, model_call_retries=0, autopilot_max_work_units=3)
    model = SequenceChatModel(
        responses=[
            call(
                "write_file", {"file_path": "/workspace/one.txt", "content": "ONE"}, "a"
            ),
            call(
                "save_task_checkpoint",
                {
                    "plan": ["one", "two"],
                    "completed": ["one"],
                    "next_step": "two",
                },
                "b",
            ),
            AIMessage(content="First step"),
            call(
                "write_file", {"file_path": "/workspace/two.txt", "content": "TWO"}, "c"
            ),
            call(
                "save_task_checkpoint",
                {
                    "plan": ["one", "two"],
                    "completed": ["one", "two"],
                    "next_step": "",
                },
                "d",
            ),
            AIMessage(content="Steps done; operator confirmation remains"),
        ]
    )
    with AgentRuntime(cfg, provider(), model=model) as runtime:
        monkeypatch.setattr(
            runtime.project_audit_store,
            "start_or_resume",
            lambda **_: pytest.fail("No audit during development"),
        )
        runtime.run_user_job(
            "Измени код проекта: создай one.txt и two.txt", allow_write=True
        )
        jobs = runtime.autopilot_store.list_jobs(workspace=cfg.workspace)
        assert len(jobs) == 1 and jobs[0]["status"] == "partial"
        detail = runtime.autopilot_store.details(jobs[0]["id"])
        assert len(detail["work_units"]) == 2
        assert all(u["phase"] == "execute" for u in detail["work_units"])
    assert (cfg.workspace / "one.txt").read_text() == "ONE"
    assert (cfg.workspace / "two.txt").read_text() == "TWO"
    with TaskStateStore(cfg.context_database) as state:
        task = state.resolve("default", cfg.workspace)
        assert task.status == "partial"
        assert state.checkpoint(task)["completed_claims"] == ["one", "two"]
        assert state.checkpoint(task)["tool_evidence"]


def test_expired_task_owner_cannot_finish_checkpoint(tmp_path):
    cfg = config_at(tmp_path)
    with TaskStateStore(cfg.context_database) as state:
        task, _ = state.prepare_turn(
            query="Измени код проекта",
            thread="web",
            workspace=cfg.workspace,
            mode="agent",
            execution="auto",
            allow_write=True,
            owner="old",
            lease_seconds=100,
        )
        with state.db:
            state.db.execute(
                "UPDATE authorized_tasks SET lease_until=0 WHERE id=?", (task.id,)
            )
        assert not state.finish(task, "completed", "Expired owner claim")
        with pytest.raises(TaskConflict):
            state.save_checkpoint(task, {"next_step": "bad"})


@pytest.mark.parametrize(
    "query,tier",
    [
        ("Привет", "fast"),
        ("Прочитай /workspace/file.py", "standard"),
        ("Удали production", "reasoning"),
        ("Продумай архитектуру", "reasoning"),
    ],
)
def test_complexity_and_risk_first(query, tier):
    assert request_signals(query).tier == tier


def test_resource_constraints_and_unknown_cost(tmp_path):
    MODEL_HEALTH.values.clear()
    cfg = config_at(tmp_path, model_profiles=json.dumps(profiles()))
    router = ResourceRouter(cfg, (provider(),))
    assert router.targets[router.choose("Привет", 100)[0]].model == "fast"
    assert (
        router.targets[router.choose("Привет", 100, tools_used=True)[0]].model
        == "standard"
    )
    with pytest.raises(NoEligibleModel):
        router.choose("Привет", 100000)
    for override in ({"model_cost_limit_usd": 0.01}, {"model_latency_budget_ms": 100}):
        with pytest.raises(NoEligibleModel):
            ResourceRouter(replace(cfg, **override), (provider(),)).choose(
                "Привет", 100
            )
    assert ResourceRouter(replace(cfg, model_local_only=True), (provider(),)).choose(
        "Привет", 100
    )
    disabled = [dict(p, enabled=False) for p in profiles()]
    with pytest.raises(NoEligibleModel):
        ResourceRouter(
            replace(cfg, model_profiles=json.dumps(disabled)), (provider(),)
        ).choose("Привет", 100)


def test_execution_escalation_uses_only_hard_filter_eligible_profile(tmp_path):
    from context_agent.runtime import ProviderFailoverMiddleware, ProviderModelTarget

    entries = [
        dict(profiles()[0], latency_ms=25.0),
        dict(profiles()[1], latency_ms=50.0),
        dict(profiles()[2], latency_ms=500.0),
    ]
    cfg = config_at(
        tmp_path,
        model_profiles=json.dumps(entries),
        model_latency_budget_ms=100,
    )
    base = provider(model="fast")
    router = ResourceRouter(cfg, (base,))
    model = SequenceChatModel(responses=[AIMessage(content="unused")])
    middleware = ProviderFailoverMiddleware(
        [ProviderModelTarget(config=target, model=model) for target in router.targets],
        router=router,
    )
    middleware.routing_query = "Привет"
    router.choose("Привет", 100)

    escalation = middleware.escalate_execution("no_verified_progress")

    assert escalation == ("lmstudio", "fast", "lmstudio", "standard")
    assert middleware.execution_tier_override == "standard"
    assert middleware.escalate_execution("no_verified_progress") is None


def test_manual_execution_escalation_requires_explicit_opt_in(tmp_path):
    from context_agent.runtime import ProviderFailoverMiddleware, ProviderModelTarget

    cfg = config_at(tmp_path, model_profiles=json.dumps(profiles()))
    base = provider(model="fast")
    router = ResourceRouter(cfg, (base,))
    model = SequenceChatModel(responses=[AIMessage(content="unused")])
    middleware = ProviderFailoverMiddleware(
        [ProviderModelTarget(config=target, model=model) for target in router.targets],
        router=router,
    )
    middleware.routing_query = "Привет"
    middleware.routing_manual = True

    assert middleware.escalate_execution("provider_timeout") is None


def test_manual_model_override_and_constrained_fallback(tmp_path):
    MODEL_HEALTH.values.clear()
    cfg = config_at(tmp_path, model_profiles=json.dumps(profiles()))
    router = ResourceRouter(cfg, (provider(model="reasoning"),))
    assert router.choose("Привет", 100, manual=True) == [0]
    assert router.metadata["mode"] == "manual"
    chosen = router.choose("Удали файл", 100)[0]
    MODEL_HEALTH.record(router.health_key(router.targets[chosen]), False, 500)
    with pytest.raises(NoEligibleModel):
        router.choose("Удали файл", 100)
    MODEL_HEALTH.values.clear()


def test_remote_lmstudio_label_cannot_bypass_local_only(tmp_path):
    cfg = config_at(
        tmp_path, model_profiles=json.dumps(profiles()), model_local_only=True
    )
    remote = replace(provider(), base_url="https://example.com/v1")
    with pytest.raises(NoEligibleModel):
        ResourceRouter(cfg, (remote,)).choose("Привет", 100)


def test_cost_latency_and_transport_health_are_applied(tmp_path):
    MODEL_HEALTH.values.clear()
    entries = [
        dict(
            profiles()[0],
            model="first",
            input_usd_per_million=1.0,
            output_usd_per_million=1.0,
            latency_ms=50.0,
        ),
        dict(
            profiles()[0],
            model="second",
            input_usd_per_million=2.0,
            output_usd_per_million=2.0,
            latency_ms=80.0,
        ),
    ]
    cfg = config_at(
        tmp_path,
        model_profiles=json.dumps(entries),
        model_cost_limit_usd=0.03,
        model_latency_budget_ms=100,
    )
    router = ResourceRouter(cfg, (provider(),))
    order = router.choose("Hi", 100)
    assert [router.targets[i].model for i in order] == ["first", "second"]
    router.activate(order[1])
    assert router.metadata["estimated_cost_usd"] == pytest.approx(0.016584)
    MODEL_HEALTH.record(router.health_key(router.targets[order[0]]), False, 10)
    assert [router.targets[i].model for i in router.choose("Hi", 100)] == ["second"]
    MODEL_HEALTH.values.clear()


def test_resource_selection_uses_real_middleware_and_does_not_replay_tools(
    tmp_path, monkeypatch
):
    MODEL_HEALTH.values.clear()
    candidates = [
        dict(profiles()[1], model="first"),
        dict(profiles()[1], model="second"),
    ]
    cfg = config_at(
        tmp_path, model_profiles=json.dumps(candidates), model_call_retries=0
    )
    first = SequenceChatModel(
        responses=[AIMessage(content="unused")], failures_remaining=1
    )
    second = SequenceChatModel(
        responses=[
            call(
                "write_file",
                {"file_path": "/workspace/selected.txt", "content": "ONCE"},
                "w1",
            ),
            AIMessage(content="Done"),
        ]
    )
    monkeypatch.setattr(
        "context_agent.runtime.create_chat_model",
        lambda p: first if p.model == "first" else second,
    )
    with AgentRuntime(cfg, provider(model="first"), model=first) as runtime:
        runtime.ask_user(
            "Создай /workspace/selected.txt с текстом ONCE", auto_context=False
        )
        writes = [
            e
            for e in runtime.last_tool_audit
            if e.name == "write_file" and e.status == "success"
        ]
        assert len(writes) == 1
        assert (
            runtime._provider_failover_middleware.runtime_metadata()["model"]
            == "second"
        )
        attempts = runtime.diagnostic_store.request(runtime.last_request_id)[
            "model_generations"
        ]
        assert [g["model"] for g in attempts] == ["first", "second", "second"]
        assert all(g["model_route"]["tier"] == "standard" for g in attempts)
    MODEL_HEALTH.values.clear()


def test_toolless_fast_profile_does_not_bind_tools(tmp_path, monkeypatch):
    cfg = config_at(tmp_path, model_profiles=json.dumps([profiles()[0]]))
    model = SequenceChatModel(responses=[AIMessage(content="Hello")])
    monkeypatch.setattr("context_agent.runtime.create_chat_model", lambda p: model)
    with AgentRuntime(cfg, provider(model="fast"), model=model) as runtime:
        runtime.ask_user("Привет", auto_context=False)
    assert not model.bound_tool_names


def test_classifier_glm_parameters_and_malformed_response_are_journaled(
    tmp_path, monkeypatch
):
    from context_agent.diagnostics import DiagnosticStore
    from context_agent.semantic_routing import semantic_classifier

    cfg = config_at(tmp_path)
    target = replace(
        provider("zhipu", "glm-5.3"), base_url="https://api.z.ai/api/paas/v4"
    )
    received = []

    class Classifier:
        response = '{"action":"resume_task","task_id":"id","confidence":0.95}'

        def invoke(self, messages, **kwargs):
            assert kwargs["max_tokens"] == 2048
            return AIMessage(content=self.response)

    def factory(p):
        received.append(p)
        return Classifier()

    monkeypatch.setattr("context_agent.semantic_routing.create_chat_model", factory)
    resolve = semantic_classifier(cfg, (target,), thread="test")
    assert resolve("Возобнови оставшуюся реализацию", ["id"]).action == "resume_task"
    assert received[0].extra_body["thinking"]["type"] == "enabled"
    assert received[0].reasoning_effort == "low"
    with DiagnosticStore(cfg.diagnostics_database) as journal:
        assert journal.list_requests(limit=1)[0]["status"] == "completed"
    Classifier.response = '{"allow_write":true}'
    assert (
        resolve_intent("Продолжи неоконченное", ["id"], semantic=resolve).action
        == "clarify"
    )
    with DiagnosticStore(cfg.diagnostics_database) as journal:
        assert journal.list_requests(limit=1)[0]["status"] == "failed"


def test_saved_task_job_identity_does_not_depend_on_resume_wording(tmp_path):
    common = dict(
        thread_id="web",
        workspace=tmp_path,
        task_identity="stable",
        workflow="project-change",
    )
    assert AutopilotStore.job_id_for(
        objective="original", allow_write=True, **common
    ) == AutopilotStore.job_id_for(objective=LONG_RESUME, allow_write=False, **common)
