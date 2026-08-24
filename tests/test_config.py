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
            "gpt-5.6-sol",
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
        (
            "zhipu",
            {"ZAI_API_KEY": "secret-zhipu"},
            "glm-5.3",
            "https://api.z.ai/api/paas/v4",
        ),
        (
            "glm",
            {"ZAI_API_KEY": "secret-glm"},
            "glm-5.3",
            "https://api.z.ai/api/paas/v4",
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


def test_glm_alias_uses_canonical_provider_name() -> None:
    config = ProviderConfig.from_env("glm", {"ZAI_API_KEY": "secret"})
    assert config.name == "zhipu"
    assert config.extra_body == {"thinking": {"type": "enabled"}}


def test_openai_gpt_5_6_disables_reasoning_for_chat_tool_calls() -> None:
    provider = ProviderConfig.from_env(
        "openai",
        {"OPENAI_API_KEY": "secret"},
    )
    model = create_chat_model(provider)
    assert provider.reasoning_effort == "none"
    assert model.reasoning_effort == "none"


def test_openai_reasoning_effort_can_be_overridden() -> None:
    provider = ProviderConfig.from_env(
        "openai",
        {
            "OPENAI_API_KEY": "secret",
            "OPENAI_REASONING_EFFORT": "low",
        },
    )
    assert provider.reasoning_effort == "low"


def test_zhipu_legacy_environment_aliases_are_supported() -> None:
    config = ProviderConfig.from_env(
        "zhipu",
        {
            "ZHIPU_API_KEY": "secret",
            "ZHIPU_MODEL": "glm-custom",
            "ZHIPU_BASE_URL": "https://example.invalid/v4/",
        },
    )
    assert config.model == "glm-custom"
    assert config.base_url == "https://example.invalid/v4"


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
        ("zhipu", {}),
        ("glm", {}),
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


def test_provider_priority_uses_explicit_order_and_canonical_alias() -> None:
    providers = ProviderConfig.priority_from_env(
        providers="openai, glm, deepseek",
        environ={
            "OPENAI_API_KEY": "openai-secret",
            "ZAI_API_KEY": "zhipu-secret",
            "DEEPSEEK_API_KEY": "deepseek-secret",
        },
    )
    assert [provider.name for provider in providers] == [
        "openai",
        "zhipu",
        "deepseek",
    ]


def test_default_provider_priority_is_glm_then_openai() -> None:
    providers = ProviderConfig.priority_from_env(
        environ={
            "ZAI_API_KEY": "zhipu-secret",
            "OPENAI_API_KEY": "openai-secret",
        }
    )
    assert [(provider.name, provider.model) for provider in providers] == [
        ("zhipu", "glm-5.3"),
        ("openai", "gpt-5.6-sol"),
    ]


def test_provider_priority_environment_precedes_legacy_default() -> None:
    providers = ProviderConfig.priority_from_env(
        environ={
            "AGENT_PROVIDER": "lmstudio",
            "AGENT_PROVIDER_PRIORITY": "qwen,openai",
            "DASHSCOPE_API_KEY": "qwen-secret",
            "OPENAI_API_KEY": "openai-secret",
        }
    )
    assert [provider.name for provider in providers] == ["qwen", "openai"]


def test_blank_environment_priority_uses_single_provider() -> None:
    providers = ProviderConfig.priority_from_env(
        environ={
            "AGENT_PROVIDER": "openai",
            "AGENT_PROVIDER_PRIORITY": "  ",
            "OPENAI_API_KEY": "openai-secret",
        }
    )
    assert [provider.name for provider in providers] == ["openai"]


@pytest.mark.parametrize("priority", ["", ",openai", "openai,", "openai,,glm"])
def test_provider_priority_rejects_empty_entries(priority: str) -> None:
    with pytest.raises(ConfigurationError, match="comma-separated"):
        ProviderConfig.priority_from_env(
            providers=priority,
            environ={"OPENAI_API_KEY": "secret", "ZAI_API_KEY": "secret"},
        )


def test_provider_priority_rejects_duplicate_aliases() -> None:
    with pytest.raises(ConfigurationError, match="repeated"):
        ProviderConfig.priority_from_env(
            providers="zhipu,glm",
            environ={"ZAI_API_KEY": "secret"},
        )


@pytest.mark.parametrize("temperature", ["0", "-0.1", "1.1"])
def test_zhipu_rejects_unsupported_temperature(temperature: str) -> None:
    with pytest.raises(ConfigurationError, match="TEMPERATURE"):
        ProviderConfig.from_env(
            "zhipu",
            {
                "ZAI_API_KEY": "secret",
                "AGENT_MODEL_TEMPERATURE": temperature,
            },
        )


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


def test_chat_model_factory_preserves_zhipu_thinking_settings() -> None:
    provider = ProviderConfig.from_env(
        "glm",
        {"ZAI_API_KEY": "secret"},
    )
    model = create_chat_model(provider)
    assert model.model_name == "glm-5.3"
    assert str(model.openai_api_base) == "https://api.z.ai/api/paas/v4"
    assert model.extra_body == {"thinking": {"type": "enabled"}}
    assert model.max_retries == 0
