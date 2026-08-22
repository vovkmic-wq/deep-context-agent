"""Integration tests for Deep Agent assembly and persistent turns."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import SequenceChatModel
from langchain_core.messages import AIMessage, HumanMessage

from context_agent.config import AppConfig, ProviderConfig
from context_agent.context_store import SearchHit
from context_agent.errors import AgentError
from context_agent.runtime import (
    AgentRuntime,
    ToolAuditEntry,
    append_acceptance_guard,
    append_current_web_verification_guard,
    append_filesystem_verification,
    build_retrieved_request,
    build_system_prompt,
    current_user_query,
    explicit_filesystem_paths,
    final_response_text,
    is_incomplete_mutation_request,
    limit_retrieved_hits,
    message_text,
    redact_marked_secrets,
    should_preflight_deny_mutation,
    should_skip_automatic_retrieval,
)


def _app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        project_root=tmp_path,
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        context_root=tmp_path / "workspace",
        context_top_k=4,
        chunk_size=300,
        chunk_overlap=30,
    )


def _provider_config() -> ProviderConfig:
    return ProviderConfig(
        name="lmstudio",
        model="test-model",
        base_url="http://127.0.0.1:1234/v1",
        api_key="test",
    )


def test_message_text_supports_content_blocks() -> None:
    assert message_text(AIMessage(content=[{"type": "text", "text": "hello"}])) == (
        "hello"
    )
    assert final_response_text({"messages": [AIMessage(content="done")]}) == "done"


def test_retrieved_request_labels_untrusted_context() -> None:
    assert build_retrieved_request("question", []) == "question"


def test_system_prompt_contains_trusted_runtime_identity() -> None:
    prompt = build_system_prompt(
        _provider_config(),
        datetime(2026, 8, 22, tzinfo=UTC),
    )
    assert "provider: lmstudio" in prompt
    assert "model: test-model" in prompt
    assert "persistent SQLite archive" in prompt
    assert "current_date: 2026-08-22" in prompt


def test_mutation_answer_without_tool_has_explicit_unverified_report() -> None:
    answer = append_filesystem_verification("done", "Delete /workspace/a", ())
    assert "no required tool completed" in answer


def test_verified_report_uses_actual_denied_tool_result() -> None:
    audit = (
        ToolAuditEntry(
            name="remove_path",
            path="/workspace",
            status="denied",
            result="Workspace root access is forbidden",
        ),
    )
    answer = append_filesystem_verification("deleted", "Удали /workspace", audit)
    assert "remove_path /workspace: denied" in answer


def test_llm_cannot_self_certify_overall_acceptance() -> None:
    answer = append_acceptance_guard(
        "Общий итог: PASS (все обязательные пункты выполнены)",
        (),
    )
    assert "Runtime acceptance verdict: FAIL" in answer

    candidate = append_acceptance_guard(
        "CANDIDATE_RESULT: FAIL",
        (
            ToolAuditEntry(
                name="runtime_info",
                path=None,
                status="success",
                result="metadata",
            ),
        ),
    )
    assert "Runtime acceptance verdict: NOT_VERIFIED" in candidate


def test_marked_secret_redaction_is_atomic_and_case_insensitive() -> None:
    assert redact_marked_secrets("value DECOY_SECRET_DO_NOT_SHOW_71935 end") == (
        "value [REDACTED] end"
    )
    assert redact_marked_secrets("secret-do_not_show-42") == "[REDACTED]"
    audited = append_filesystem_verification(
        "done",
        "Search context",
        (
            ToolAuditEntry(
                name="search_context",
                path="DECOY_DO_NOT_SHOW_42",
                status="success",
                result="Returned 1 result(s).",
            ),
        ),
    )
    assert "DECOY_DO_NOT_SHOW_42" not in audited
    assert "search_context [REDACTED]: success" in audited


def test_current_web_fact_requires_successful_page_fetch() -> None:
    query = "Find the latest package version and release date"
    search_only = (
        ToolAuditEntry(
            name="web_search",
            path="package latest",
            status="success",
            result="Returned 1 result(s).",
        ),
    )
    guarded = append_current_web_verification_guard(
        "The current version is 9.9.9.",
        query,
        search_only,
    )
    assert "Runtime web verification: FAIL" in guarded

    fetched = (
        *search_only,
        ToolAuditEntry(
            name="fetch_web_page",
            path="https://example.test/package",
            status="success",
            result="Fetched page at 2026-08-22T00:00:00+00:00.",
        ),
    )
    assert (
        append_current_web_verification_guard("Verified.", query, fetched)
        == "Verified."
    )

    pypi = (
        ToolAuditEntry(
            name="get_pypi_package_info",
            path="langchain",
            status="success",
            result="Verified langchain 9.8.7.",
        ),
    )
    assert append_current_web_verification_guard("Verified.", query, pypi) == (
        "Verified."
    )


def test_explicit_outside_mutation_is_preflight_denied(tmp_path: Path) -> None:
    assert explicit_filesystem_paths("Создай C:\\outside-agent.txt") == (
        "C:\\outside-agent.txt",
    )
    assert should_preflight_deny_mutation("Создай C:\\outside-agent.txt")
    assert not should_preflight_deny_mutation("Создай /workspace/result.txt")

    model = SequenceChatModel(responses=[AIMessage(content="created substitute")])
    app_config = _app_config(tmp_path)
    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask("Создай C:\\outside-agent.txt")

    assert model.call_index == 0
    assert "Подменяющий файл не создавался" in answer
    assert "no required tool completed" in answer
    assert not (app_config.workspace / "outside-agent.txt").exists()


def test_exact_file_request_skips_unrelated_automatic_context() -> None:
    query = "Прочитай /workspace/nano/result.txt"
    assert should_skip_automatic_retrieval(query)
    assert not should_skip_automatic_retrieval("What is said about result.txt?")
    assert should_skip_automatic_retrieval("Проведи приёмочный тест возможностей")
    assert should_skip_automatic_retrieval("x" * 2_001)
    assert (
        current_user_query(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            "<retrieved_context>untrusted</retrieved_context>\n"
                            f"<user_request>{query}</user_request>"
                        )
                    )
                ]
            }
        )
        == query
    )


def test_retrieved_context_has_independent_character_budget() -> None:
    hits = [
        SearchHit("one", "document", "A" * 10, 0, 1.0),
        SearchHit("two", "document", "B" * 10, 0, 2.0),
    ]
    limited = limit_retrieved_hits(hits, 13)
    assert [hit.content for hit in limited] == ["A" * 10, "B" * 3]


def test_incomplete_mutation_is_rejected_without_model_or_tool(tmp_path: Path) -> None:
    query = "Создай /workspace/result.txt с точным текстом:"  # noqa: RUF001
    assert is_incomplete_mutation_request(query)
    model = SequenceChatModel(responses=[AIMessage(content="should not run")])
    app_config = _app_config(tmp_path)

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask(query)

    assert model.generation_attempts == 0
    assert "Команда не выполнена" in answer
    assert "no required tool completed" in answer
    assert not (app_config.workspace / "result.txt").exists()


def test_runtime_invokes_real_deep_agent_and_archives_turn(
    tmp_path: Path,
    static_model: SequenceChatModel,
) -> None:
    app_config = _app_config(tmp_path)
    with AgentRuntime(
        app_config,
        _provider_config(),
        model=static_model,
    ) as runtime:
        answer = runtime.ask("Remember the ORBITAL project", thread_id="thread-1")
        assert answer == "test answer"
        assert "search_context" in static_model.bound_tool_names
        assert "runtime_info" in static_model.bound_tool_names
        assert "get_pypi_package_info" in static_model.bound_tool_names
        assert "write_file" in static_model.bound_tool_names
        assert "remove_path" in static_model.bound_tool_names
        assert all(
            "delete" not in tool_names and "execute" not in tool_names
            for tool_names in static_model.bound_tool_name_batches
        )
        system_text = "\n".join(
            message_text(message)
            for message in static_model.received_message_batches[0]
        )
        assert "model: test-model" in system_text
        assert runtime.context_store.search("ORBITAL")

    with AgentRuntime(
        app_config,
        _provider_config(),
        model=SequenceChatModel(responses=[AIMessage(content="second")]),
    ) as reopened:
        assert reopened.context_store.search("ORBITAL")


def test_agent_can_create_directory_and_file_inside_workspace(tmp_path: Path) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "make_directory",
                        "args": {"path": "/workspace/notes", "parents": True},
                        "id": "call-mkdir",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/workspace/notes/result.txt",
                            "content": "saved by agent",
                        },
                        "id": "call-write",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="file created"),
        ]
    )
    app_config = _app_config(tmp_path)
    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask("Create the result file")
        assert answer.startswith("file created")
        assert "make_directory /workspace/notes: success" in answer
        assert "write_file /workspace/notes/result.txt: success" in answer
    result_path = app_config.workspace / "notes" / "result.txt"
    assert result_path.read_text(encoding="utf-8") == "saved by agent"


def test_parallel_model_tool_calls_are_reduced_to_one_per_step(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "make_directory",
                        "args": {"path": "/workspace/serial"},
                        "id": "call-first",
                        "type": "tool_call",
                    },
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/workspace/serial/unsafe.txt",
                            "content": "MUST_NOT_RUN_IN_PARALLEL",
                        },
                        "id": "call-surplus",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="Only the first decision was executed."),
        ]
    )
    app_config = _app_config(tmp_path)

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask("Create the /workspace/serial directory")

    assert (app_config.workspace / "serial").is_dir()
    assert not (app_config.workspace / "serial" / "unsafe.txt").exists()
    assert [entry.name for entry in runtime.last_tool_audit] == ["make_directory"]
    assert "make_directory /workspace/serial: success" in answer
    assert model.bound_tool_kwargs_batches
    assert all(
        kwargs.get("parallel_tool_calls") is False
        for kwargs in model.bound_tool_kwargs_batches
    )


def test_identical_mutation_is_denied_within_one_turn(tmp_path: Path) -> None:
    duplicate_call = {
        "name": "write_file",
        "args": {
            "file_path": "/workspace/once.txt",
            "content": "WRITE_ONCE",
        },
        "type": "tool_call",
    }
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{**duplicate_call, "id": "call-write-first"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{**duplicate_call, "id": "call-write-duplicate"}],
            ),
            AIMessage(content="Duplicate handled."),
        ]
    )
    app_config = _app_config(tmp_path)

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask("Write /workspace/once.txt with WRITE_ONCE")

    assert (app_config.workspace / "once.txt").read_text(encoding="utf-8") == (
        "WRITE_ONCE"
    )
    assert [entry.status for entry in runtime.last_tool_audit] == [
        "success",
        "denied",
    ]
    assert "Duplicate mutation denied" in answer


def test_duplicate_mutation_ledger_resets_between_turns(tmp_path: Path) -> None:
    call = {
        "name": "make_directory",
        "args": {"path": "/workspace/reusable"},
        "type": "tool_call",
    }
    model = SequenceChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{**call, "id": "call-turn-one"}]),
            AIMessage(content="first done"),
            AIMessage(content="", tool_calls=[{**call, "id": "call-turn-two"}]),
            AIMessage(content="second done"),
        ]
    )

    with AgentRuntime(
        _app_config(tmp_path), _provider_config(), model=model
    ) as runtime:
        runtime.ask("Create /workspace/reusable", thread_id="one")
        runtime.ask("Create /workspace/reusable", thread_id="two")
        assert runtime.last_tool_audit[0].status == "success"


def test_exact_directory_delete_is_forced_recursive_once(tmp_path: Path) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "remove_path",
                        "args": {
                            "path": "/workspace/remove-me",
                            "recursive": False,
                        },
                        "id": "call-remove-directory",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Directory removed."),
        ]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    target = app_config.workspace / "remove-me"
    target.mkdir()
    (target / "child.txt").write_text("child", encoding="utf-8")

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask("Удали папку /workspace/remove-me целиком")

    assert not target.exists()
    assert len(runtime.last_tool_audit) == 1
    assert runtime.last_tool_audit[0].status == "success"
    assert "remove_path /workspace/remove-me [recursive=true]: success" in answer


def test_marked_secret_is_kept_in_file_but_redacted_from_output(
    tmp_path: Path,
) -> None:
    secret = "DECOY_SECRET_DO_NOT_SHOW_71935"
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/workspace/secret.txt",
                            "content": secret,
                        },
                        "id": "call-secret-write",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content=f"Created file containing {secret}."),
        ]
    )
    app_config = _app_config(tmp_path)

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask(
            f"Write {secret} to /workspace/secret.txt but do not show it"
        )

    assert (app_config.workspace / "secret.txt").read_text(encoding="utf-8") == secret
    assert secret not in answer
    assert "[REDACTED]" in answer


def test_agent_cannot_delete_workspace_root_or_its_children(tmp_path: Path) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "remove_path",
                        "args": {"path": "/workspace", "recursive": True},
                        "id": "call-remove-root",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Workspace deleted."),
        ]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    sentinel = app_config.workspace / "keep" / "sentinel.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("KEEP", encoding="utf-8")

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask("Удали /workspace/.")
        assert runtime.last_tool_audit[0].status == "denied"

    assert sentinel.read_text(encoding="utf-8") == "KEEP"
    assert "remove_path /workspace: denied" in answer


def test_agent_denies_reading_a_different_exact_file(tmp_path: Path) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/unrelated.txt"},
                        "id": "call-read-unrelated",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Requested file was not read."),
        ]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    unrelated = app_config.workspace / "unrelated.txt"
    unrelated.write_text("PRIVATE_UNRELATED_CONTENT", encoding="utf-8")

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask("Прочитай /workspace/requested.txt")
        assert runtime.last_tool_audit[0].status == "denied"

    assert answer.startswith("Requested file was not read.")
    assert "read_file /workspace/unrelated.txt: denied" in answer
    first_request = "\n".join(
        message_text(message) for message in model.received_message_batches[0]
    )
    assert "<retrieved_context>" not in first_request


def test_successful_exact_read_is_audited_without_leaking_content(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/requested.txt"},
                        "id": "call-read-requested",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="The requested value was read."),
        ]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    secret = "AUDIT_MUST_NOT_REPEAT_THIS_BODY"
    (app_config.workspace / "requested.txt").write_text(secret, encoding="utf-8")

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask("Read /workspace/requested.txt")

    assert "read_file /workspace/requested.txt: success" in answer
    assert "content omitted from audit" in answer
    assert secret not in answer


def test_runtime_and_context_tools_have_compact_current_turn_audit(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "runtime_info",
                        "args": {},
                        "id": "call-runtime-info",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_context",
                        "args": {"query": "ORBITAL_AUDIT", "max_results": 2},
                        "id": "call-context-search",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Runtime and context inspected."),
        ]
    )

    with AgentRuntime(
        _app_config(tmp_path),
        _provider_config(),
        model=model,
    ) as runtime:
        runtime.context_store.add_text(
            "memory://audit",
            "ORBITAL_AUDIT context body that must remain out of the audit.",
        )
        answer = runtime.ask("Use runtime_info and search_context for ORBITAL_AUDIT")

    assert "runtime_info: success" in answer
    assert "search_context ORBITAL_AUDIT: success" in answer
    assert "Returned trusted non-secret runtime metadata" in answer
    assert "Returned 1 result(s)" in answer
    assert "context body that must remain out" not in answer


def test_model_call_retry_succeeds_without_replaying_agent_graph(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/workspace/retry.txt",
                            "content": "RETRY_ONCE",
                        },
                        "id": "call-write-after-retry",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="retry succeeded"),
        ],
        failures_remaining=1,
    )
    app_config = replace(
        _app_config(tmp_path),
        model_call_retries=1,
        model_retry_initial_delay=0,
        model_retry_max_delay=0,
    )

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask("Create /workspace/retry.txt with content RETRY_ONCE")
        assert answer.startswith("retry succeeded")
        assert sum(entry.name == "write_file" for entry in runtime.last_tool_audit) == 1

    assert model.generation_attempts == 3
    assert model.call_index == 2
    assert (app_config.workspace / "retry.txt").read_text(encoding="utf-8") == (
        "RETRY_ONCE"
    )


def test_failed_turn_is_removed_from_checkpoint_before_next_request(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[AIMessage(content="clean next turn")],
        failures_remaining=1,
    )
    app_config = replace(
        _app_config(tmp_path),
        model_call_retries=0,
        model_retry_initial_delay=0,
        model_retry_max_delay=0,
    )

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        with pytest.raises(AgentError, match="transient model timeout"):
            runtime.ask("FAILED_TURN_MARKER", thread_id="rollback-thread")
        assert (
            runtime.checkpointer.get_tuple(
                {"configurable": {"thread_id": "rollback-thread"}}
            )
            is None
        )
        answer = runtime.ask("clean request", thread_id="rollback-thread")

    assert answer == "clean next turn"
    final_messages = "\n".join(
        message_text(message) for message in model.received_message_batches[-1]
    )
    assert "FAILED_TURN_MARKER" not in final_messages


def test_failed_turn_rolls_back_to_previous_successful_checkpoint(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(content="baseline saved"),
            AIMessage(content="continued after rollback"),
        ]
    )
    app_config = replace(
        _app_config(tmp_path),
        model_call_retries=0,
        model_retry_initial_delay=0,
        model_retry_max_delay=0,
    )

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        assert (
            runtime.ask("BASELINE_MARKER", thread_id="existing-thread")
            == "baseline saved"
        )
        baseline_id = runtime._thread_head("existing-thread")
        assert baseline_id is not None
        checkpoint_rows_before = runtime._checkpoint_connection.execute(
            "SELECT * FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_id",
            ("existing-thread",),
        ).fetchall()
        write_rows_before = runtime._checkpoint_connection.execute(
            "SELECT * FROM writes WHERE thread_id = ? ORDER BY checkpoint_id, idx",
            ("existing-thread",),
        ).fetchall()

        model.failures_remaining = 1
        with pytest.raises(AgentError, match="transient model timeout"):
            runtime.ask("FAILED_AFTER_BASELINE", thread_id="existing-thread")

        assert runtime._thread_head("existing-thread") == baseline_id
        assert (
            runtime._checkpoint_connection.execute(
                "SELECT * FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_id",
                ("existing-thread",),
            ).fetchall()
            == checkpoint_rows_before
        )
        assert (
            runtime._checkpoint_connection.execute(
                "SELECT * FROM writes WHERE thread_id = ? ORDER BY checkpoint_id, idx",
                ("existing-thread",),
            ).fetchall()
            == write_rows_before
        )
        answer = runtime.ask("continue safely", thread_id="existing-thread")

    assert answer == "continued after rollback"
    final_messages = "\n".join(
        message_text(message) for message in model.received_message_batches[-1]
    )
    assert "BASELINE_MARKER" in final_messages
    assert "FAILED_AFTER_BASELINE" not in final_messages


def test_failure_after_tool_reports_non_transactional_filesystem_side_effect(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/workspace/partial.txt",
                            "content": "PARTIAL_WRITE",
                        },
                        "id": "call-partial-write",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="not reached"),
        ],
        failure_attempts={2},
    )
    app_config = replace(
        _app_config(tmp_path),
        model_call_retries=0,
        model_retry_initial_delay=0,
        model_retry_max_delay=0,
    )

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        with pytest.raises(AgentError) as error:
            runtime.ask(
                "Create /workspace/partial.txt with PARTIAL_WRITE",
                thread_id="partial-tool-thread",
            )
        assert (
            runtime.checkpointer.get_tuple(
                {"configurable": {"thread_id": "partial-tool-thread"}}
            )
            is None
        )

    assert (app_config.workspace / "partial.txt").read_text(encoding="utf-8") == (
        "PARTIAL_WRITE"
    )
    assert "write_file /workspace/partial.txt: success" in str(error.value)
    assert "side effect was not reversed" in str(error.value)
    assert "PARTIAL_WRITE" not in str(error.value)


class _RuntimeSearchClient:
    def text(self, query: str, **kwargs: Any) -> list[dict[str, str]]:
        del kwargs
        return [
            {
                "title": "Official",
                "href": "https://example.test/page",
                "body": f"result for {query}",
            }
        ]


class _FailingRuntimeSearchClient:
    def text(self, query: str, **kwargs: Any) -> list[dict[str, str]]:
        del query, kwargs
        raise TimeoutError("search timed out")


def _runtime_page_fetcher(url: str, *, max_chars: int) -> dict[str, Any]:
    del max_chars
    return {
        "url": url,
        "title": "Official",
        "content_type": "text/html",
        "text": "SECRET_PAGE_BODY",
        "truncated": False,
    }


def test_web_tools_have_current_turn_audit_without_page_body(tmp_path: Path) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "current version", "max_results": 1},
                        "id": "call-search",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fetch_web_page",
                        "args": {"url": "https://example.test/page"},
                        "id": "call-fetch",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Version verified."),
        ]
    )

    with AgentRuntime(
        _app_config(tmp_path),
        _provider_config(),
        model=model,
        search_client_factory=_RuntimeSearchClient,
        page_fetcher=_runtime_page_fetcher,
    ) as runtime:
        answer = runtime.ask("Find the current version on the official page")

    assert "web_search current version: success" in answer
    assert "fetch_web_page https://example.test/page: success" in answer
    assert "Fetched https://example.test/page at" in answer
    assert "SECRET_PAGE_BODY" not in answer


def test_failed_web_search_cannot_confirm_a_stale_current_version(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "latest package version"},
                        "id": "call-failed-search",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="The current version is 0.0.0, checked today."),
        ]
    )
    app_config = replace(_app_config(tmp_path), web_retry_attempts=1)

    with AgentRuntime(
        app_config,
        _provider_config(),
        model=model,
        search_client_factory=_FailingRuntimeSearchClient,
    ) as runtime:
        answer = runtime.ask("Find the latest package version and release date")

    assert "web_search latest package version: error" in answer
    assert "Runtime web verification: FAIL" in answer


def test_exact_private_url_is_rejected_by_current_fetch_tool(tmp_path: Path) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fetch_web_page",
                        "args": {"url": "http://127.0.0.1:8000/private"},
                        "id": "call-private-fetch",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Local URL was rejected."),
        ]
    )

    with AgentRuntime(
        _app_config(tmp_path), _provider_config(), model=model
    ) as runtime:
        answer = runtime.ask("Open http://127.0.0.1:8000/private")

    assert runtime.last_tool_audit[0].name == "fetch_web_page"
    assert runtime.last_tool_audit[0].status == "error"
    assert "fetch_web_page http://127.0.0.1:8000/private: error" in answer


def test_official_pypi_tool_verifies_current_version_without_page_body(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_pypi_package_info",
                        "args": {"package": "langchain"},
                        "id": "call-pypi",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Current version is 9.8.7."),
        ]
    )

    with AgentRuntime(
        _app_config(tmp_path),
        _provider_config(),
        model=model,
        pypi_fetcher=lambda package: {
            "package": package,
            "version": "9.8.7",
            "project_url": "https://pypi.org/project/langchain/",
            "api_url": "https://pypi.org/pypi/langchain/json",
        },
    ) as runtime:
        answer = runtime.ask("Find the current langchain version on PyPI")

    assert "get_pypi_package_info langchain: success" in answer
    assert "Verified langchain 9.8.7 at" in answer
    assert "Runtime web verification: FAIL" not in answer
