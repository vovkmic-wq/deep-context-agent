"""Tests for provider and application configuration."""

from pathlib import Path

import pytest

from context_agent.config import AppConfig, ProviderConfig
from context_agent.errors import ConfigurationError
from context_agent.providers import create_chat_model


@pytest.mark.parametrize(
    ("provider", "environment", "expected_model", "expected_url"),
    [
        (
            "lmstudio",
            {},
            "local-model",
            "http://127.0.0.1:1234/v1",
        ),
        (
            "openai",
            {"OPENAI_API_KEY": "secret-openai"},
            "gpt-5.5",
            "https://api.openai.com/v1",
        ),
        (
            "yandex",
            {
                "YANDEX_API_KEY": "secret-yandex",
                "YANDEX_FOLDER_ID": "folder-1",
            },
            "gpt://folder-1/yandexgpt/latest",
            "https://ai.api.cloud.yandex.net/v1",
        ),
        (
            "deepseek",
            {"DEEPSEEK_API_KEY": "secret-deepseek"},
            "deepseek-v4-flash",
            "https://api.deepseek.com",
        ),
        (
            "qwen",
            {"DASHSCOPE_API_KEY": "secret-qwen"},
            "qwen3.7-plus",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        ),
    ],
)
def test_provider_configuration(
    provider: str,
    environment: dict[str, str],
    expected_model: str,
    expected_url: str,
) -> None:
    config = ProviderConfig.from_env(provider, environment)
    assert config.model == expected_model
    assert config.base_url == expected_url
    assert "secret" not in repr(config)


def test_yandex_explicit_model_uri_takes_precedence() -> None:
    config = ProviderConfig.from_env(
        "yandex",
        {
            "YANDEX_API_KEY": "secret",
            "YANDEX_FOLDER_ID": "ignored",
            "YANDEX_MODEL_URI": "gpt://folder/custom/latest",
        },
    )
    assert config.model == "gpt://folder/custom/latest"


@pytest.mark.parametrize(
    ("provider", "environment"),
    [
        ("openai", {}),
        ("yandex", {"YANDEX_FOLDER_ID": "folder"}),
        ("deepseek", {}),
        ("qwen", {}),
    ],
)
def test_remote_provider_requires_key(
    provider: str,
    environment: dict[str, str],
) -> None:
    with pytest.raises(ConfigurationError):
        ProviderConfig.from_env(provider, environment)


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="Unknown provider"):
        ProviderConfig.from_env("unknown", {})


def test_app_config_resolves_and_prepares_paths(tmp_path: Path) -> None:
    config = AppConfig.from_env(
        tmp_path,
        {
            "AGENT_WORKSPACE": "work",
            "AGENT_DATA_DIR": "data",
            "AGENT_CONTEXT_ROOT": "context",
            "AGENT_CONTEXT_TOP_K": "9",
            "AGENT_AUTO_CONTEXT_MAX_CHARS": "6000",
            "AGENT_AUTO_CONTEXT_QUERY_MAX_CHARS": "1500",
            "AGENT_CONTEXT_MAX_FILE_MB": "64",
            "AGENT_MODEL_CALL_RETRIES": "4",
            "AGENT_MODEL_RETRY_INITIAL_DELAY": "0.5",
            "AGENT_MODEL_RETRY_MAX_DELAY": "5",
            "AGENT_WEB_RETRY_ATTEMPTS": "2",
        },
    )
    config.prepare_directories()
    assert config.workspace == (tmp_path / "work").resolve()
    assert config.context_top_k == 9
    assert config.auto_context_max_chars == 6000
    assert config.auto_context_query_max_chars == 1500
    assert config.max_file_bytes == 64 * 1024 * 1024
    assert config.model_call_retries == 4
    assert config.model_retry_initial_delay == 0.5
    assert config.model_retry_max_delay == 5
    assert config.web_retry_attempts == 2
    assert config.workspace.is_dir()
    assert config.data_dir.is_dir()
    assert config.context_root.is_dir()


def test_legacy_retrieval_limit_alias_is_supported(tmp_path: Path) -> None:
    legacy = AppConfig.from_env(tmp_path, {"AGENT_RETRIEVAL_LIMIT": "4"})
    canonical = AppConfig.from_env(
        tmp_path,
        {"AGENT_RETRIEVAL_LIMIT": "4", "AGENT_CONTEXT_TOP_K": "7"},
    )
    assert legacy.context_top_k == 4
    assert canonical.context_top_k == 7


def test_chat_model_factory_preserves_compatible_settings() -> None:
    provider = ProviderConfig.from_env(
        "deepseek",
        {"DEEPSEEK_API_KEY": "secret"},
    )
    model = create_chat_model(provider)
    assert model.model_name == "deepseek-v4-flash"
    assert str(model.openai_api_base) == "https://api.deepseek.com"
    assert model.extra_body == {"thinking": {"type": "disabled"}}
    assert model.max_retries == 0
