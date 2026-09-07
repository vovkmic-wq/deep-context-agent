"""Production orchestration regressions introduced in version 0.28."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import SequenceChatModel
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from context_agent.config import AppConfig, ProviderConfig
from context_agent.project_checks import ProjectCheckResult
from context_agent.runtime import (
    AgentRuntime,
    ProviderFailoverMiddleware,
    ProviderModelTarget,
)
from context_agent.schema_contract import build_schema_contract


def _provider(name: str, model: str) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        model=model,
        base_url=f"https://{name}.example/v1",
        api_key="test",
    )


def test_provider_circuit_skips_timed_out_primary_on_next_call() -> None:
    primary = SequenceChatModel(responses=[AIMessage(content="unused")])
    fallback = SequenceChatModel(responses=[AIMessage(content="unused")])
    targets = [
        ProviderModelTarget(_provider("zhipu", "glm-test"), primary),
        ProviderModelTarget(_provider("openai", "gpt-test"), fallback),
    ]
    middleware = ProviderFailoverMiddleware(
        targets,
        circuit_failure_threshold=1,
        circuit_cooldown_seconds=300,
    )
    request = ModelRequest(model=primary, messages=[], tools=[])
    primary_calls = 0

    def handle(current: ModelRequest) -> ModelResponse:
        nonlocal primary_calls
        if current.model is primary:
            primary_calls += 1
            raise TimeoutError("provider timed out")
        return ModelResponse(result=[AIMessage(content="fallback")])

    assert middleware.wrap_model_call(request, handle).result[0].content == "fallback"
    middleware.reset()
    assert middleware.wrap_model_call(request, handle).result[0].content == "fallback"

    assert primary_calls == 1
    assert any(attempt.status == "circuit_open" for attempt in middleware.attempts)
    assert middleware.runtime_metadata()["provider_circuits"][0]["state"] == "open"


def test_schema_contract_extracts_fields_without_file_bodies(tmp_path: Path) -> None:
    schema = tmp_path / "src" / "models.py"
    schema.parent.mkdir(parents=True)
    schema.write_text(
        "class ProductSnapshot:\n"
        "    product_id: int\n"
        "    collected_at: str\n\n"
        "SECRET_BODY = 'must not appear in contract'\n",
        encoding="utf-8",
    )

    payload = json.loads(build_schema_contract(tmp_path).to_json())

    assert payload["files_scanned"] == 1
    product = next(
        item for item in payload["symbols"] if item["name"] == "ProductSnapshot"
    )
    assert [field["name"] for field in product["fields"]] == [
        "product_id",
        "collected_at",
    ]
    assert "must not appear" not in json.dumps(payload)


def test_work_unit_objective_and_schema_are_bounded() -> None:
    request = AgentRuntime._build_conversational_work_unit(
        "X" * 1_000_000,
        "project-change",
        phase="implement",
        schema_contract="Y" * 12_000,
    )

    assert len(request) < 30_000
    assert "objective truncated for this unit" in request
    assert "Runtime schema contract" in request


def test_project_checks_are_allowed_without_project_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_project_checks",
                        "args": {"checks": "pytest"},
                        "id": "checks-without-scan",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Runtime check evidence received."),
        ]
    )
    config = AppConfig(
        project_root=tmp_path,
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        context_root=tmp_path / "workspace",
    )
    result = ProjectCheckResult(
        check="pytest",
        command=("python", "-m", "pytest"),
        return_code=0,
        duration_seconds=0.01,
        status="passed",
        output="1 passed",
    )
    with AgentRuntime(config, _provider("lmstudio", "local"), model=model) as runtime:
        monkeypatch.setattr(runtime.project_check_runner, "run", lambda _="": [result])
        runtime.set_routing_scope(
            workspace_reads_allowed=True,
            project_scan_allowed=False,
            project_checks_allowed=True,
        )
        answer = runtime.ask("Run pytest for /workspace/src/app.py")

    assert answer.startswith("Runtime check evidence received.")
    assert any(
        entry.name == "run_project_checks" and entry.status == "success"
        for entry in runtime.last_tool_audit
    )
