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
        # Retries are owned by ModelRetryMiddleware so a whole agent graph and
        # already completed tools can never be replayed by provider settings.
        "max_retries": 0,
    }
    if config.extra_body:
        kwargs["extra_body"] = config.extra_body
    return ChatOpenAI(**kwargs)
