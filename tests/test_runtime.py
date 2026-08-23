"""Integration tests for Deep Agent assembly and persistent turns."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import SequenceChatModel
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from context_agent.config import AppConfig, ProviderConfig
from context_agent.context_store import SearchHit
from context_agent.errors import AgentError
from context_agent.runtime import (
    AcceptanceManifest,
    AgentRuntime,
    ExplicitToolBudgetMiddleware,
    SequentialToolCallMiddleware,
    ToolAuditEntry,
    acceptance_forced_tool_choice,
    append_acceptance_guard,
    append_current_web_verification_guard,
    append_filesystem_verification,
    append_result_cardinality_guard,
    build_retrieved_request,
    build_system_prompt,
    current_user_query,
    evaluate_acceptance_manifest,
    explicit_filesystem_paths,
    explicit_tool_call_budget,
    final_response_text,
    forbidden_mutation_paths,
    forbidden_read_paths,
    format_acceptance_evaluation,
    is_incomplete_mutation_request,
    limit_retrieved_hits,
    message_text,
    parse_acceptance_manifest,
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
    assert "Runtime acceptance verdict: NOT_VERIFIED" in answer

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


def test_acceptance_manifest_is_strict_bounded_and_machine_readable() -> None:
    payload = {
        "version": 1,
        "exact_tool_call_counts": {"remove_path": 1, "read_file": 1},
        "required_events": [
            {
                "id": "removed",
                "tool": "remove_path",
                "target": "/workspace/a",
                "statuses": ["success"],
            },
            {
                "id": "missing_after_remove",
                "tool": "read_file",
                "target": "/workspace/a/result.txt",
                "statuses": ["error", "not_found"],
                "after": "removed",
            },
        ],
        "forbidden_events": [
            {
                "id": "no_decoy_read",
                "tool": "read_file",
                "target": "/workspace/a/decoy.txt",
                "statuses": ["success", "error", "denied", "not_found"],
            }
        ],
        "pending_requirements": ["restart memory check"],
        "allowed_unlisted_tools": ["write_todos"],
    }
    query = (
        "Run acceptance.\n<acceptance_manifest>\n"
        f"{json.dumps(payload)}\n"
        "</acceptance_manifest>"
    )

    manifest = parse_acceptance_manifest(query)

    assert isinstance(manifest, AcceptanceManifest)
    assert manifest.exact_tool_call_counts == {"remove_path": 1, "read_file": 1}
    assert manifest.allowed_unlisted_tools == ("write_todos",)
    with pytest.raises(ValueError, match="unknown tool"):
        parse_acceptance_manifest(
            '<acceptance_manifest>{"exact_tool_call_counts": '
            '{"shell": 1}}</acceptance_manifest>'
        )
    with pytest.raises(ValueError, match="invalid statuses"):
        parse_acceptance_manifest(
            """<acceptance_manifest>{
            "required_events": [{"id": "bad_status", "tool": "read_file",
            "statuses": ["maybe"]}]}</acceptance_manifest>"""
        )
    with pytest.raises(ValueError, match="unknown manifest field"):
        parse_acceptance_manifest(
            '<acceptance_manifest>{"unexpected": true, '
            '"exact_tool_call_counts": {"read_file": 1}}</acceptance_manifest>'
        )
    with pytest.raises(ValueError, match="unknown after dependency"):
        parse_acceptance_manifest(
            """<acceptance_manifest>{
            "required_events": [{"id": "child", "tool": "read_file",
            "after": "missing_parent"}]}</acceptance_manifest>"""
        )
    oversized = "x" * 20_001
    with pytest.raises(ValueError, match="20000-character"):
        parse_acceptance_manifest(
            f'<acceptance_manifest>{{"padding":"{oversized}"}}</acceptance_manifest>'
        )
    with pytest.raises(ValueError, match="unknown tool"):
        parse_acceptance_manifest(
            '<acceptance_manifest>{"exact_tool_call_counts": {"read_file": 1},'
            '"allowed_unlisted_tools": ["shell"]}</acceptance_manifest>'
        )
    with pytest.raises(ValueError, match="duplicate tool"):
        parse_acceptance_manifest(
            '<acceptance_manifest>{"exact_tool_call_counts": {"read_file": 1},'
            '"allowed_unlisted_tools": ["write_todos", "write_todos"]}'
            "</acceptance_manifest>"
        )
    with pytest.raises(ValueError, match="cannot overlap"):
        parse_acceptance_manifest(
            '<acceptance_manifest>{"exact_tool_call_counts": {"read_file": 1},'
            '"allowed_unlisted_tools": ["read_file"]}</acceptance_manifest>'
        )


def test_acceptance_manifest_v2_validates_structured_evidence() -> None:
    digest = hashlib.sha256(b"EXACT_BODY").hexdigest()
    manifest = parse_acceptance_manifest(
        f"""<acceptance_manifest>{{
        "version": 2,
        "exact_tool_call_counts": {{"read_file": 1, "search_context": 1}},
        "required_events": [
          {{"id": "read", "tool": "read_file",
           "target": "/workspace/result.txt", "content_sha256": "{digest}"}},
          {{"id": "search", "tool": "search_context",
           "target": "CONTROL", "min_results": 1, "after": "read"}}
        ]}}</acceptance_manifest>"""
    )

    assert manifest is not None
    assert manifest.required_events[0].content_sha256 == digest
    assert manifest.required_events[1].min_results == 1

    invalid_manifests = [
        (
            "require version 2",
            """<acceptance_manifest>{"version": 1,
            "required_events": [{"id": "search", "tool": "search_context",
            "min_results": 1}]}</acceptance_manifest>""",
        ),
        (
            "invalid min_results",
            """<acceptance_manifest>{"version": 2,
            "required_events": [{"id": "search", "tool": "search_context",
            "min_results": -1}]}</acceptance_manifest>""",
        ),
        (
            "uses min_results",
            """<acceptance_manifest>{"version": 2,
            "required_events": [{"id": "read", "tool": "read_file",
            "min_results": 1}]}</acceptance_manifest>""",
        ),
        (
            "invalid content_sha256",
            """<acceptance_manifest>{"version": 2,
            "required_events": [{"id": "read", "tool": "read_file",
            "content_sha256": "bad"}]}</acceptance_manifest>""",
        ),
        (
            "forbidden events cannot define",
            f"""<acceptance_manifest>{{"version": 2,
            "forbidden_events": [{{"id": "read", "tool": "read_file",
            "content_sha256": "{digest}"}}]}}</acceptance_manifest>""",
        ),
    ]
    for expected_error, payload in invalid_manifests:
        with pytest.raises(ValueError, match=expected_error):
            parse_acceptance_manifest(payload)


def test_acceptance_manifest_v2_evaluates_count_and_content_hash() -> None:
    digest = hashlib.sha256(b"EXACT_BODY").hexdigest()
    manifest = parse_acceptance_manifest(
        f"""<acceptance_manifest>{{
        "version": 2,
        "exact_tool_call_counts": {{"read_file": 1, "search_context": 1}},
        "required_events": [
          {{"id": "read", "tool": "read_file",
           "target": "/workspace/result.txt", "content_sha256": "{digest}"}},
          {{"id": "search", "tool": "search_context", "target": "CONTROL",
           "min_results": 1, "after": "read"}}
        ]}}</acceptance_manifest>"""
    )
    assert manifest is not None
    good = evaluate_acceptance_manifest(
        manifest,
        (
            ToolAuditEntry(
                "read_file",
                "/workspace/result.txt",
                "success",
                "read",
                content_sha256=digest,
            ),
            ToolAuditEntry(
                "search_context",
                "CONTROL",
                "success",
                "Returned 4 result(s).",
                result_count=4,
            ),
        ),
    )
    bad = evaluate_acceptance_manifest(
        manifest,
        (
            ToolAuditEntry(
                "read_file",
                "/workspace/result.txt",
                "success",
                "read",
                content_sha256="0" * 64,
            ),
            ToolAuditEntry(
                "search_context",
                "CONTROL",
                "success",
                "Returned 0 result(s).",
                result_count=0,
            ),
        ),
    )

    assert (good.failed, good.blocked) == (0, 0)
    assert bad.failed == 1
    assert bad.blocked == 1
    assert "content_sha256=expected" in bad.failures[0]


def test_acceptance_manifest_evaluates_order_counts_and_forbidden_events() -> None:
    manifest = parse_acceptance_manifest(
        """<acceptance_manifest>
        {
          "exact_tool_call_counts": {"remove_path": 1, "read_file": 1},
          "required_events": [
            {"id": "removed", "tool": "remove_path",
             "target": "/workspace/a", "statuses": ["success"]},
            {"id": "missing", "tool": "read_file",
             "target": "/workspace/a/result.txt",
             "statuses": ["error", "not_found"], "after": "removed"}
          ],
          "forbidden_events": [
            {"id": "no_decoy", "tool": "read_file",
             "target": "/workspace/a/decoy.txt",
             "statuses": ["success", "error", "denied", "not_found"]}
          ],
          "pending_requirements": ["restart"]
        }
        </acceptance_manifest>"""
    )
    assert manifest is not None
    good_audit = (
        ToolAuditEntry("remove_path", "/workspace/a [recursive=true]", "success", "ok"),
        ToolAuditEntry("read_file", "/workspace/a/result.txt", "error", "missing"),
    )

    good = evaluate_acceptance_manifest(manifest, good_audit)
    bad = evaluate_acceptance_manifest(
        manifest,
        (
            ToolAuditEntry("read_file", "/workspace/a/result.txt", "error", "missing"),
            ToolAuditEntry("remove_path", "/workspace/a", "success", "ok"),
            ToolAuditEntry("read_file", "/workspace/a/decoy.txt", "denied", "blocked"),
            ToolAuditEntry("write_todos", None, "success", "extra"),
        ),
    )

    assert (good.passed, good.failed, good.pending) == (5, 0, 1)
    assert bad.failed >= 4
    assert any("unexpected tool write_todos" in failure for failure in bad.failures)
    report = append_acceptance_guard(
        "LLM_OBSERVATION_ONLY: 99 PASS, 0 FAIL",
        good_audit,
        """<acceptance_manifest>
        {"exact_tool_call_counts": {"remove_path": 1, "read_file": 1}}
        </acceptance_manifest>""",
    )
    assert "Tool call counts:" in report
    assert "Runtime acceptance verdict: PASS" in report


def test_allowed_unlisted_tool_is_reported_without_failing_manifest() -> None:
    manifest = parse_acceptance_manifest(
        """<acceptance_manifest>{
        "exact_tool_call_counts": {"runtime_info": 1},
        "allowed_unlisted_tools": ["write_todos"]
        }</acceptance_manifest>"""
    )
    assert manifest is not None

    evaluation = evaluate_acceptance_manifest(
        manifest,
        (
            ToolAuditEntry("write_todos", None, "error", "bad plan"),
            ToolAuditEntry("write_todos", None, "success", "plan"),
            ToolAuditEntry("runtime_info", None, "success", "metadata"),
        ),
    )

    assert evaluation.failed == 0
    assert evaluation.blocked == 0
    assert evaluation.tool_counts["write_todos"] == 2


def test_missing_required_event_blocks_dependents_without_cascade_failures() -> None:
    manifest = parse_acceptance_manifest(
        """<acceptance_manifest>{
        "exact_tool_call_counts": {"write_file": 0, "read_file": 0},
        "required_events": [
          {"id": "write", "tool": "write_file", "target": "/workspace/a"},
          {"id": "read", "tool": "read_file", "target": "/workspace/a",
           "after": "write"}
        ]}</acceptance_manifest>"""
    )
    assert manifest is not None

    evaluation = evaluate_acceptance_manifest(manifest, ())

    assert evaluation.failed == 1
    assert evaluation.blocked == 1
    assert evaluation.failures == (
        "event write: missing write_file target=/workspace/a "
        "with status in ['success']",
    )
    assert "dependency write was not observed" in evaluation.blocked_requirements[0]
    report = format_acceptance_evaluation(evaluation)
    assert "1 FAIL, 1 BLOCKED" in report
    assert "Runtime acceptance verdict: FAIL" in report


def test_forbidden_read_paths_maps_a_basename_to_an_explicit_path() -> None:
    query = (
        "Создай /workspace/a/decoy.txt.\n"
        "Прочитай /workspace/a/result.txt.\n"
        "Не читай и не показывай decoy.txt."  # noqa: RUF001
    )

    assert forbidden_read_paths(query) == frozenset({"/workspace/a/decoy.txt"})


def test_forbidden_read_paths_supports_multiple_files_in_negative_clause() -> None:
    query = (
        "Файлы /workspace/a/one.txt, /workspace/a/two.txt и "
        "/workspace/a/result.txt существуют.\n"
        "Не читай one.txt или two.txt. Проверь result.txt."  # noqa: RUF001
    )

    assert forbidden_read_paths(query) == frozenset(
        {"/workspace/a/one.txt", "/workspace/a/two.txt"}
    )


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


def test_acceptance_manifest_version_does_not_trigger_web_guard() -> None:
    query = """Сообщи текущее число результатов поиска памяти.
    <acceptance_manifest>
    {"version": 1, "exact_tool_call_counts": {"search_context": 1}}
    </acceptance_manifest>"""

    assert append_current_web_verification_guard("4", query, ()) == "4"


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


def test_sequential_middleware_omits_parallel_setting_without_tools() -> None:
    model = SequenceChatModel(responses=[AIMessage(content="done")])
    request = ModelRequest(
        model=model,
        messages=[],
        tools=[],
        model_settings={"parallel_tool_calls": False},
    )
    captured: dict[str, Any] = {}

    def handler(current: ModelRequest) -> ModelResponse:
        captured.update(current.model_settings)
        return ModelResponse(result=[AIMessage(content="done")])

    response = SequentialToolCallMiddleware().wrap_model_call(request, handler)

    assert response.result[0].content == "done"
    assert "parallel_tool_calls" not in captured


def test_budget_precedes_sequential_normalization_for_empty_toolset() -> None:
    model = SequenceChatModel(responses=[AIMessage(content="done")])
    first_call = {
        "name": "runtime_info",
        "args": {},
        "id": "call-order-first",
        "type": "tool_call",
    }
    second_call = {
        "name": "list_context_sources",
        "args": {},
        "id": "call-order-second",
        "type": "tool_call",
    }
    state = {
        "messages": [
            HumanMessage(content="Use at most 2 functional tool calls."),
            AIMessage(content="", tool_calls=[first_call]),
            ToolMessage(
                content='{"status":"success"}',
                tool_call_id=first_call["id"],
                name=first_call["name"],
            ),
            AIMessage(content="", tool_calls=[second_call]),
            ToolMessage(
                content='{"status":"success","sources":[]}',
                tool_call_id=second_call["id"],
                name=second_call["name"],
            ),
        ]
    }
    request = ModelRequest(
        model=model,
        messages=[],
        tools=[{"name": "runtime_info"}, {"name": "search_context"}],
        state=state,
        model_settings={"parallel_tool_calls": False},
    )
    captured: dict[str, Any] = {}

    def final_handler(current: ModelRequest) -> ModelResponse:
        captured["tools"] = current.tools
        captured["tool_choice"] = current.tool_choice
        captured["model_settings"] = current.model_settings
        return ModelResponse(result=[AIMessage(content="done")])

    sequential = SequentialToolCallMiddleware()
    response = ExplicitToolBudgetMiddleware().wrap_model_call(
        request,
        lambda budgeted: sequential.wrap_model_call(budgeted, final_handler),
    )

    assert response.result[0].content == "done"
    assert captured["tools"] == []
    assert captured["tool_choice"] is None
    assert "parallel_tool_calls" not in captured["model_settings"]


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


def test_duplicate_read_only_and_web_calls_are_denied_within_turn(
    tmp_path: Path,
) -> None:
    calls = [
        {
            "name": "runtime_info",
            "args": {},
            "type": "tool_call",
        },
        {
            "name": "get_pypi_package_info",
            "args": {"package": "langchain"},
            "type": "tool_call",
        },
        {
            "name": "fetch_web_page",
            "args": {"url": "https://example.test/source", "max_chars": 12000},
            "type": "tool_call",
        },
        {
            "name": "list_context_sources",
            "args": {"limit": 100, "offset": 0, "kind": ""},
            "type": "tool_call",
        },
    ]
    responses: list[AIMessage] = []
    for index, call in enumerate(calls):
        responses.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[{**call, "id": f"call-{index}-first"}],
                ),
                AIMessage(
                    content="",
                    tool_calls=[{**call, "id": f"call-{index}-duplicate"}],
                ),
            ]
        )
    responses.append(AIMessage(content="Repeated calls handled."))
    model = SequenceChatModel(responses=responses)

    with AgentRuntime(
        _app_config(tmp_path),
        _provider_config(),
        model=model,
        pypi_fetcher=lambda package: {
            "package": package,
            "version": "1.2.3",
            "project_url": "https://pypi.org/project/langchain/",
            "api_url": "https://pypi.org/pypi/langchain/json",
        },
        page_fetcher=lambda url, max_chars: {
            "url": url,
            "title": "Source",
            "content_type": "text/plain",
            "text": "verified",
            "truncated": len("verified") > max_chars,
        },
    ) as runtime:
        runtime.ask(
            "Acceptance: call runtime_info, get_pypi_package_info for langchain, "
            "fetch_web_page for https://example.test/source, and "
            "list_context_sources; deliberately retry every call."
        )

    assert [entry.status for entry in runtime.last_tool_audit] == [
        "success",
        "denied",
        "success",
        "denied",
        "success",
        "denied",
        "success",
        "denied",
    ]


def test_third_read_without_path_mutation_is_denied(tmp_path: Path) -> None:
    read_call = {
        "name": "read_file",
        "args": {"file_path": "/workspace/repeat.txt"},
        "type": "tool_call",
    }
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{**read_call, "id": f"read-{index}"}],
            )
            for index in range(3)
        ]
        + [AIMessage(content="Reads complete.")]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    (app_config.workspace / "repeat.txt").write_text("VALUE", encoding="utf-8")

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        runtime.ask("Прочитай /workspace/repeat.txt и проверь результат.")

    assert [entry.status for entry in runtime.last_tool_audit] == [
        "success",
        "success",
        "denied",
    ]


def test_recursive_parent_removal_opens_a_new_child_read_state(
    tmp_path: Path,
) -> None:
    read_call = {
        "name": "read_file",
        "args": {"file_path": "/workspace/tree/value.txt"},
        "type": "tool_call",
    }
    model = SequenceChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{**read_call, "id": "read-one"}]),
            AIMessage(content="", tool_calls=[{**read_call, "id": "read-two"}]),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "remove_path",
                        "args": {"path": "/workspace/tree", "recursive": False},
                        "id": "remove-tree",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[{**read_call, "id": "read-after-remove"}],
            ),
            AIMessage(content="Cleanup checked."),
        ]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    tree = app_config.workspace / "tree"
    tree.mkdir()
    (tree / "value.txt").write_text("VALUE", encoding="utf-8")

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        runtime.ask(
            "Прочитай /workspace/tree/value.txt для проверки, затем удали "
            "папку /workspace/tree целиком и проверь чтением, что файл отсутствует."
        )

    assert [entry.status for entry in runtime.last_tool_audit] == [
        "success",
        "success",
        "success",
        "error",
    ]
    assert not tree.exists()


def test_read_only_call_ledger_resets_between_turns(tmp_path: Path) -> None:
    runtime_call = {
        "name": "runtime_info",
        "args": {},
        "type": "tool_call",
    }
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{**runtime_call, "id": "runtime-turn-one"}]
            ),
            AIMessage(content="first"),
            AIMessage(
                content="", tool_calls=[{**runtime_call, "id": "runtime-turn-two"}]
            ),
            AIMessage(content="second"),
        ]
    )

    with AgentRuntime(
        _app_config(tmp_path), _provider_config(), model=model
    ) as runtime:
        runtime.ask("Call runtime_info once", thread_id="turn-one")
        runtime.ask("Call runtime_info once", thread_id="turn-two")

    assert runtime.last_tool_audit == (
        ToolAuditEntry(
            "runtime_info",
            None,
            "success",
            "Returned trusted non-secret runtime metadata.",
        ),
    )


def test_explicitly_forbidden_decoy_read_is_denied(tmp_path: Path) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/a/result.txt"},
                        "id": "read-allowed-result",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/a/decoy.txt"},
                        "id": "read-forbidden-decoy",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Decoy was not read."),
        ]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    target = app_config.workspace / "a"
    target.mkdir()
    secret = "DECOY_SECRET_DO_NOT_SHOW_71935"
    (target / "decoy.txt").write_text(secret, encoding="utf-8")
    (target / "result.txt").write_text("SAFE", encoding="utf-8")

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask(
            "Файлы /workspace/a/result.txt и /workspace/a/decoy.txt существуют.\n"
            "Не читай decoy.txt и не показывай его содержимое. "  # noqa: RUF001
            "Проверь result.txt."
        )

    assert [entry.status for entry in runtime.last_tool_audit] == [
        "success",
        "denied",
    ]
    assert "read_file /workspace/a/result.txt: success" in answer
    assert "explicitly forbade" in answer
    assert secret not in answer


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


def test_acceptance_completion_gate_finishes_ordered_cleanup(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "remove_path",
                        "args": {"path": "/workspace/tree", "recursive": False},
                        "id": "remove-tree",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/sentinel.txt"},
                        "id": "wrong-next-read",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Premature final answer two."),
            AIMessage(content="Premature final answer three."),
            AIMessage(content="Cleanup complete."),
        ]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    tree = app_config.workspace / "tree"
    tree.mkdir()
    (tree / "result.txt").write_text("VALUE", encoding="utf-8")
    sentinel = app_config.workspace / "sentinel.txt"
    sentinel.write_text("KEEP", encoding="utf-8")
    query = """Modify only explicitly named paths. Remove the directory
    /workspace/tree recursively by calling remove_path, then call read_file for
    /workspace/tree/result.txt. Call remove_path for
    /workspace/sentinel.txt, then call read_file for that exact path.
    <acceptance_manifest>
    {
      "version": 1,
      "exact_tool_call_counts": {"remove_path": 2, "read_file": 2},
      "required_events": [
        {"id": "tree_removed", "tool": "remove_path",
         "target": "/workspace/tree", "statuses": ["success"]},
        {"id": "result_absent", "tool": "read_file",
         "target": "/workspace/tree/result.txt",
         "statuses": ["error", "not_found"], "after": "tree_removed"},
        {"id": "sentinel_removed", "tool": "remove_path",
         "target": "/workspace/sentinel.txt", "statuses": ["success"],
         "after": "result_absent"},
        {"id": "sentinel_absent", "tool": "read_file",
         "target": "/workspace/sentinel.txt",
         "statuses": ["error", "not_found"], "after": "sentinel_removed"}
      ]
    }
    </acceptance_manifest>"""

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask(query)

    assert [(entry.name, entry.status) for entry in runtime.last_tool_audit] == [
        ("remove_path", "success"),
        ("read_file", "error"),
        ("remove_path", "success"),
        ("read_file", "error"),
    ]
    assert not tree.exists()
    assert not sentinel.exists()
    assert "Runtime acceptance verdict: PASS" in answer
    assert model.call_index == 5
    assert [batch.get("tool_choice") for batch in model.bound_tool_kwargs_batches] == [
        None,
        "read_file",
        "remove_path",
        "read_file",
        None,
    ]


def test_acceptance_completion_gate_never_starts_a_root_event(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(responses=[AIMessage(content="No tool call.")])
    query = """Проверь /workspace/missing.txt.
    <acceptance_manifest>
    {
      "version": 1,
      "exact_tool_call_counts": {"read_file": 1},
      "required_events": [
        {"id": "initial_read", "tool": "read_file",
         "target": "/workspace/missing.txt",
         "statuses": ["error", "not_found"]}
      ]
    }
    </acceptance_manifest>"""

    with AgentRuntime(
        _app_config(tmp_path), _provider_config(), model=model
    ) as runtime:
        answer = runtime.ask(query)

    assert runtime.last_tool_audit == ()
    assert "Runtime acceptance verdict: FAIL" in answer


def test_acceptance_tool_choice_accepts_natural_filesystem_intent() -> None:
    query = """Create /workspace/sentinel.txt with the control text.
    <acceptance_manifest>
    {
      "version": 1,
      "exact_tool_call_counts": {"runtime_info": 1, "write_file": 1},
      "required_events": [
        {"id": "runtime", "tool": "runtime_info", "statuses": ["success"]},
        {"id": "sentinel", "tool": "write_file",
         "target": "/workspace/sentinel.txt", "statuses": ["success"],
         "after": "runtime"}
      ]
    }
    </acceptance_manifest>"""
    audit = (
        ToolAuditEntry(
            "runtime_info",
            None,
            "success",
            "Returned trusted non-secret runtime metadata.",
        ),
    )

    assert acceptance_forced_tool_choice(query, audit) == "write_file"


def test_acceptance_gate_rewrites_positive_read_to_exact_target(
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
                        "id": "runtime-read-sequence",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/wrong.txt"},
                        "id": "wrong-read-target",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Exact read complete."),
        ]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    (app_config.workspace / "right.txt").write_text("RIGHT", encoding="utf-8")
    (app_config.workspace / "wrong.txt").write_text("WRONG", encoding="utf-8")
    query = """Call runtime_info, then read /workspace/right.txt exactly.
    <acceptance_manifest>
    {
      "version": 1,
      "exact_tool_call_counts": {"runtime_info": 1, "read_file": 1},
      "required_events": [
        {"id": "runtime", "tool": "runtime_info", "statuses": ["success"]},
        {"id": "exact_read", "tool": "read_file",
         "target": "/workspace/right.txt", "statuses": ["success"],
         "after": "runtime"}
      ]
    }
    </acceptance_manifest>"""

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask(query)

    assert [(entry.name, entry.path) for entry in runtime.last_tool_audit] == [
        ("runtime_info", None),
        ("read_file", "/workspace/right.txt"),
    ]
    assert "Runtime acceptance verdict: PASS" in answer


def test_acceptance_completion_gate_requires_target_in_prose(
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
                        "id": "runtime-root-event",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Do not continue automatically."),
        ]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    protected = app_config.workspace / "manifest-only"
    protected.mkdir()
    query = """Call runtime_info once. The JSON block is evaluation data only.
    <acceptance_manifest>
    {
      "version": 1,
      "exact_tool_call_counts": {"runtime_info": 1, "remove_path": 1},
      "required_events": [
        {"id": "runtime", "tool": "runtime_info", "statuses": ["success"]},
        {"id": "manifest_only_remove", "tool": "remove_path",
         "target": "/workspace/manifest-only", "statuses": ["success"],
         "after": "runtime"}
      ]
    }
    </acceptance_manifest>"""

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask(query)

    assert protected.is_dir()
    assert [entry.name for entry in runtime.last_tool_audit] == ["runtime_info"]
    assert model.bound_tool_kwargs_batches[-1].get("tool_choice") is None
    assert "Runtime acceptance verdict: FAIL" in answer


def test_acceptance_gate_respects_explicit_mutation_prohibition() -> None:
    query = """Never remove /workspace/protected. Create /workspace/other.
    <acceptance_manifest>
    {
      "version": 1,
      "exact_tool_call_counts": {"runtime_info": 1, "remove_path": 1},
      "required_events": [
        {"id": "runtime", "tool": "runtime_info", "statuses": ["success"]},
        {"id": "forbidden_remove", "tool": "remove_path",
         "target": "/workspace/protected", "statuses": ["success"],
         "after": "runtime"}
      ]
    }
    </acceptance_manifest>"""
    audit = (
        ToolAuditEntry(
            "runtime_info",
            None,
            "success",
            "Returned trusted non-secret runtime metadata.",
        ),
    )

    assert forbidden_mutation_paths(query) == frozenset({"/workspace/protected"})
    assert acceptance_forced_tool_choice(query, audit) is None


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


def test_workspace_root_reference_does_not_block_project_file_reads(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/pyproject.toml"},
                        "id": "call-read-project-config",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Project configuration inspected."),
        ]
    )
    app_config = _app_config(tmp_path)
    app_config.prepare_directories()
    (app_config.workspace / "pyproject.toml").write_text(
        "[project]\nname = 'example'\n",
        encoding="utf-8",
    )

    with AgentRuntime(app_config, _provider_config(), model=model) as runtime:
        answer = runtime.ask(
            "Inspect the Python project available inside /workspace/. "
            "Read its configuration before reporting."
        )
        assert runtime.last_tool_audit[0].status == "success"

    assert answer.startswith("Project configuration inspected.")
    assert "read_file /workspace/pyproject.toml: success" in answer


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
    assert (
        runtime.last_tool_audit[0].content_sha256
        == hashlib.sha256(secret.encode("utf-8")).hexdigest()
    )


def test_result_cardinality_guard_replaces_incorrect_model_count(
    tmp_path: Path,
) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_context",
                        "args": {"query": "CARDINALITY_CONTROL", "max_results": 4},
                        "id": "call-cardinality",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Точное количество результатов: 0."),
        ]
    )

    with AgentRuntime(
        _app_config(tmp_path), _provider_config(), model=model
    ) as runtime:
        runtime.context_store.add_text(
            "memory://cardinality",
            "CARDINALITY_CONTROL persistent evidence",
        )
        answer = runtime.ask(
            "Вызови search_context для CARDINALITY_CONTROL и сообщи точное "
            "количество результатов из ToolMessage."
        )

    assert runtime.last_tool_audit[0].result_count == 1
    assert answer.startswith(
        "Точное количество результатов search_context по ToolMessage: 1."
    )
    assert "Точное количество результатов: 0" not in answer


def test_cardinality_guard_lists_all_structured_counts() -> None:
    answer = append_result_cardinality_guard(
        "Inspected.",
        "Search and list sources",
        (
            ToolAuditEntry(
                "search_context",
                "CONTROL",
                "success",
                "Returned 4 result(s).",
                result_count=4,
            ),
            ToolAuditEntry(
                "list_context_sources",
                None,
                "success",
                "Returned 7 source(s).",
                result_count=7,
            ),
        ),
    )

    assert "search_context: 4" in answer
    assert "list_context_sources: 7" in answer


def test_explicit_exact_once_suppresses_second_provider_call(
    tmp_path: Path,
) -> None:
    call = {
        "name": "search_context",
        "args": {"query": "EXACT_ONCE_CONTROL", "max_results": 4},
        "type": "tool_call",
    }
    model = SequenceChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{**call, "id": "call-first"}]),
            AIMessage(content="", tool_calls=[{**call, "id": "call-stale-repeat"}]),
            AIMessage(content="This response must not be needed."),
        ]
    )

    with AgentRuntime(
        _app_config(tmp_path), _provider_config(), model=model
    ) as runtime:
        runtime.context_store.add_text(
            "memory://exact-once",
            "EXACT_ONCE_CONTROL persistent evidence",
        )
        answer = runtime.ask(
            "Вызови search_context ровно один раз с запросом "  # noqa: RUF001
            "EXACT_ONCE_CONTROL и сообщи точное количество результатов "
            "из ToolMessage."
        )

    assert len(runtime.last_tool_audit) == 1
    assert runtime.last_tool_audit[0].name == "search_context"
    assert runtime.last_tool_audit[0].status == "success"
    assert runtime.last_tool_audit[0].result_count == 1
    assert model.call_index == 2
    assert "search_context" not in model.bound_tool_name_batches[-1]
    assert answer.startswith(
        "Точное количество результатов search_context по ToolMessage: 1."
    )


def test_explicit_tool_call_budget_parses_ozon_prompt_limits() -> None:
    budget = explicit_tool_call_budget(
        "Используй не более 15 функциональных tool calls за весь ход. "
        "Выполни не более двух узких\n"
        "  `search_context(max_results=5)`."
    )

    assert budget.total == 15
    assert budget.per_tool == {"search_context": 2}


def test_explicit_per_tool_budget_suppresses_stale_provider_call(
    tmp_path: Path,
) -> None:
    calls = [
        {
            "name": "search_context",
            "args": {"query": f"BUDGET_CONTROL_{index}", "max_results": 4},
            "id": f"call-budget-{index}",
            "type": "tool_call",
        }
        for index in range(3)
    ]
    model = SequenceChatModel(
        responses=[
            AIMessage(content="", tool_calls=[calls[0]]),
            AIMessage(content="", tool_calls=[calls[1]]),
            AIMessage(content="", tool_calls=[calls[2]]),
            AIMessage(content="This response must not be needed."),
        ]
    )

    with AgentRuntime(
        _app_config(tmp_path), _provider_config(), model=model
    ) as runtime:
        answer = runtime.ask(
            "Call search_context no more than two times, using a different "
            "BUDGET_CONTROL query each time, then answer."
        )

    assert [entry.name for entry in runtime.last_tool_audit] == [
        "search_context",
        "search_context",
    ]
    assert "search_context" not in model.bound_tool_name_batches[-1]
    assert "tool-call budget is exhausted" in answer


def test_explicit_total_tool_budget_suppresses_third_tool(tmp_path: Path) -> None:
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "runtime_info",
                        "args": {},
                        "id": "call-total-runtime",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_context_sources",
                        "args": {},
                        "id": "call-total-list",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_context",
                        "args": {"query": "must not execute"},
                        "id": "call-total-stale",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )

    with AgentRuntime(
        _app_config(tmp_path), _provider_config(), model=model
    ) as runtime:
        answer = runtime.ask("Use at most 2 functional tool calls, then answer.")

    assert [entry.name for entry in runtime.last_tool_audit] == [
        "runtime_info",
        "list_context_sources",
    ]
    assert "tool-call budget is exhausted" in answer


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
