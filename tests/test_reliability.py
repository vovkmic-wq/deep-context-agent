"""Actual model attempts, bounded recovery and false-positive protection."""

import json
import threading
from dataclasses import replace

import pytest
from conftest import SequenceChatModel
from langchain_core.messages import AIMessage

from context_agent.config import AppConfig, ProviderConfig
from context_agent.diagnostics import classify_failure
from context_agent.errors import AgentError
from context_agent.reliability import repeated_prose
from context_agent.runtime import AgentRuntime


def settings(tmp_path, **changes):
    return AppConfig(
        project_root=tmp_path,
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        context_root=tmp_path / "workspace",
        model_retry_initial_delay=0,
        model_retry_max_delay=0,
        **changes,
    )


def provider():
    return ProviderConfig(
        name="lmstudio",
        model="fixture",
        api_key="secret-test-key",
        base_url="http://localhost:1234/v1",
    )


def test_prose_detector_preserves_structured_output():
    paragraph = (
        "This is a sufficiently long paragraph describing the same failure "
        "again and again without any new information."
    )
    assert repeated_prose("\n\n".join([paragraph] * 4))
    assert not repeated_prose("\n\n".join([paragraph] * 3))
    assert not repeated_prose("```python\n" + (paragraph + "\n\n") * 5 + "```")
    assert not repeated_prose(json.dumps({"data": [paragraph] * 40}))
    assert not repeated_prose(("| column | " + paragraph + " |\n\n") * 20)


def test_responses_incomplete_output_is_not_accepted(tmp_path):
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="unfinished",
                response_metadata={
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                },
            )
        ]
    )
    with (
        AgentRuntime(settings(tmp_path), provider(), model=model) as runtime,
        pytest.raises(AgentError) as caught,
    ):
        runtime.ask("Answer briefly", auto_context=False)
    assert classify_failure(caught.value) == "generation_truncated"
    assert model.generation_attempts == 1


def test_cancel_after_verified_write_prevents_next_edit_and_keeps_evidence(tmp_path):
    cancelled = threading.Event()

    class CancelModel(SequenceChatModel):
        def _generate(self, *args, **kwargs):
            response = super()._generate(*args, **kwargs)
            if self.generation_attempts == 2:
                cancelled.set()
            return response

    model = CancelModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "w1",
                        "name": "write_file",
                        "args": {"file_path": "/workspace/kept.txt", "content": "KEPT"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "e1",
                        "name": "edit_file",
                        "args": {
                            "file_path": "/workspace/kept.txt",
                            "old_string": "KEPT",
                            "new_string": "WRONG",
                        },
                    }
                ],
            ),
        ]
    )
    config = settings(tmp_path)
    with AgentRuntime(config, provider(), model=model) as runtime:
        runtime.set_turn_controls(cancelled=cancelled)
        with pytest.raises(AgentError) as caught:
            runtime.ask(
                "Create the requested file.",
                auto_context=False,
                diagnostic_request_id="cancel-after-write",
            )
        journal = runtime.diagnostic_store.request("cancel-after-write")
    assert classify_failure(caught.value) == "task_cancelled"
    assert (config.workspace / "kept.txt").read_text() == "KEPT"
    assert journal["filesystem_side_effects"] is True
    assert (
        journal["model_generations"][-1]["cancellation"]
        == "local_observed_remote_unconfirmed"
    )


def test_actual_retry_attempts_and_unknown_usage_are_durable(tmp_path):
    model = SequenceChatModel(failures_remaining=1, responses=[AIMessage(content="OK")])
    with AgentRuntime(settings(tmp_path), provider(), model=model) as runtime:
        runtime.ask(
            "Answer briefly.", auto_context=False, diagnostic_request_id="retry"
        )
        record = runtime.diagnostic_store.request("retry")
        attempts = record["model_generations"]
    assert len(attempts) == 2
    assert [a["status"] for a in attempts] == ["error", "success"]
    assert attempts[1]["retry_count"] == 1
    assert attempts[1]["input_tokens"] is None
    assert all(a["created_at_utc"] and a["finished_at_utc"] for a in attempts)
    assert all(len(a["prompt_sha256"]) == 64 for a in attempts)
    assert "secret-test-key" not in json.dumps(record)


@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_repetition_observation_and_opt_in_stop(tmp_path, mode):
    paragraph = (
        "The model has repeated this entire paragraph without adding any "
        "useful information to the final answer."
    )
    model = SequenceChatModel(responses=[AIMessage(content=(paragraph + "\n\n") * 5)])
    with AgentRuntime(
        settings(tmp_path, repetition_mode=mode), provider(), model=model
    ) as runtime:
        if mode == "enforce":
            with pytest.raises(AgentError) as caught:
                runtime.ask("Answer briefly.", diagnostic_request_id="repeat")
            assert classify_failure(caught.value) == "generation_repetition"
        else:
            runtime.ask("Answer briefly.", diagnostic_request_id="repeat")
        record = runtime.diagnostic_store.request("repeat")
        assert record["model_generations"][0]["repetition_detected"]
        assert model.generation_attempts == 1


def test_truncated_tool_call_never_mutates_file_or_retries(tmp_path):
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                response_metadata={"finish_reason": "length"},
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/workspace/bad.txt",
                            "content": "incomplete",
                        },
                        "id": "cut",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    with AgentRuntime(settings(tmp_path), provider(), model=model) as runtime:
        with pytest.raises(AgentError) as caught:
            runtime.ask("Answer briefly.", diagnostic_request_id="truncated")
        assert classify_failure(caught.value) == "generation_truncated"
        assert model.generation_attempts == 1
        assert not (runtime.app_config.workspace / "bad.txt").exists()
        record = runtime.diagnostic_store.request("truncated")
        assert record["rollback_success"]
        assert record["model_generations"][0]["finish_reason"] == "length"


def test_cancel_before_generation_and_before_side_effect(tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    model = SequenceChatModel(responses=[AIMessage(content="must not run")])
    with AgentRuntime(settings(tmp_path), provider(), model=model) as runtime:
        runtime.set_turn_controls(cancelled=cancelled)
        with pytest.raises(AgentError) as caught:
            runtime.ask("Answer briefly.")
        assert classify_failure(caught.value) == "task_cancelled"
        assert model.generation_attempts == 0


def test_no_progress_and_model_budget_stop_without_graph_replay(tmp_path):
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "runtime_info",
                "args": {},
                "id": "loop",
                "type": "tool_call",
            }
        ],
    )
    for name, values, expected in [
        ("progress", {"no_progress_limit": 2}, "no_verified_progress"),
        ("budget", {"task_model_attempts": 2}, "model_attempt_budget"),
    ]:
        model = SequenceChatModel(responses=[message])
        with AgentRuntime(
            settings(tmp_path / name, **values), provider(), model=model
        ) as runtime:
            with pytest.raises(AgentError) as caught:
                runtime.ask("Answer briefly.")
            assert classify_failure(caught.value) == expected
            assert model.generation_attempts == 2


def test_deadline_before_model_call(tmp_path):
    model = SequenceChatModel(responses=[AIMessage(content="must not run")])
    with AgentRuntime(settings(tmp_path), provider(), model=model) as runtime:
        runtime._persistent_budget = True
        runtime._reliability.deadline = 0
        with pytest.raises(AgentError) as caught:
            runtime.ask("Answer briefly.")
        assert classify_failure(caught.value) == "task_deadline"
        assert not model.generation_attempts


def test_persistent_budget_ignores_legacy_deadline_but_enforces_unit(tmp_path):
    model = SequenceChatModel(responses=[AIMessage(content="must not run")])
    with AgentRuntime(settings(tmp_path), provider(), model=model) as runtime:
        runtime._persistent_budget = True
        runtime._reliability.start_budget(persistent=True)
        assert runtime._reliability.deadline is None
        runtime._reliability.begin_unit(8, timeout_seconds=30)
        runtime._reliability.unit_deadline = 0
        with pytest.raises(AgentError) as caught:
            runtime.ask("Answer briefly.")
        assert classify_failure(caught.value) == "unit_deadline"
        assert not model.generation_attempts


def test_configuration_validates_reliability_limits(tmp_path):
    with pytest.raises(AgentError):
        replace(settings(tmp_path), model_output_tokens=-1)
    with pytest.raises(AgentError):
        replace(settings(tmp_path), repetition_mode="silent-retry-forever")
