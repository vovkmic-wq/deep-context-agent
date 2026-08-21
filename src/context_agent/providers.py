"""Factory for all supported OpenAI-compatible chat providers."""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from context_agent.config import ProviderConfig


def create_chat_model(config: ProviderConfig) -> ChatOpenAI:
    """Create a LangChain model suitable for Deep Agents tool calling."""
    kwargs: dict[str, object] = {
        "model": config.model,
        "api_key": SecretStr(config.api_key),
        "base_url": config.base_url,
        "temperature": config.temperature,
        "timeout": config.timeout,
        "max_retries": config.max_retries,
    }
    if config.extra_body:
        kwargs["extra_body"] = config.extra_body
    return ChatOpenAI(**kwargs)
