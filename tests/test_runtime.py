"""Integration tests for Deep Agent assembly and persistent turns."""

from pathlib import Path

from conftest import SequenceChatModel
from langchain_core.messages import AIMessage

from context_agent.config import AppConfig, ProviderConfig
from context_agent.runtime import (
    AgentRuntime,
    build_retrieved_request,
    final_response_text,
    message_text,
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
        assert "write_file" in static_model.bound_tool_names
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
        assert runtime.ask("Create the result file") == "file created"
    result_path = app_config.workspace / "notes" / "result.txt"
    assert result_path.read_text(encoding="utf-8") == "saved by agent"
