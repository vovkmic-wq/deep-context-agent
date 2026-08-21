"""Shared test doubles and fixtures."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class SequenceChatModel(BaseChatModel):
    """Deterministic tool-capable chat model for agent integration tests."""

    responses: list[AIMessage]
    call_index: int = 0
    bound_tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "sequence-test-model"

    def bind_tools(
        self,
        tools: Sequence[Any],
        **_: Any,
    ) -> SequenceChatModel:
        self.bound_tool_names = [
            tool.name if hasattr(tool, "name") else str(tool) for tool in tools
        ]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        index = min(self.call_index, len(self.responses) - 1)
        self.call_index += 1
        return ChatResult(generations=[ChatGeneration(message=self.responses[index])])


@pytest.fixture
def static_model() -> SequenceChatModel:
    """Return a model that immediately emits a final response."""
    return SequenceChatModel(responses=[AIMessage(content="test answer")])
