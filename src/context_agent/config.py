"""Environment-driven configuration for the application and LLM providers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from context_agent.errors import ConfigurationError

CANONICAL_PROVIDERS = (
    "lmstudio",
    "openai",
    "yandex",
    "deepseek",
    "qwen",
    "zhipu",
)
PROVIDER_ALIASES = {"glm": "zhipu"}
SUPPORTED_PROVIDERS = (*CANONICAL_PROVIDERS, *PROVIDER_ALIASES)
DEFAULT_PROVIDER_PRIORITY = ("glm", "openai")


def _absolute_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _float_setting(environ: Mapping[str, str], name: str, default: float) -> float:
    raw_value = environ.get(name, str(default))
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


def _int_setting(environ: Mapping[str, str], name: str, default: int) -> int:
    raw_value = environ.get(name, str(default))
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _int_setting_with_alias(
    environ: Mapping[str, str],
    name: str,
    alias: str,
    default: int,
) -> int:
    """Read a canonical integer setting, falling back to a legacy alias."""

    raw_value = environ.get(name, environ.get(alias, str(default)))
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _pattern_setting(environ: Mapping[str, str], name: str) -> tuple[str, ...]:
    """Read comma/newline-separated non-empty glob patterns."""

    raw = environ.get(name, "")
    return tuple(
        item.strip()
        for line in raw.splitlines()
        for item in line.split(",")
        if item.strip()
    )


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Resolved settings for one OpenAI-compatible chat model."""

    name: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    temperature: float = 0.1
    timeout: float = 120.0
    extra_body: dict[str, Any] = field(default_factory=dict)
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        if self.name not in CANONICAL_PROVIDERS:
            supported = ", ".join(SUPPORTED_PROVIDERS)
            raise ConfigurationError(
                f"Unknown provider '{self.name}'. Supported: {supported}"
            )
        if not self.model.strip():
            raise ConfigurationError(f"Model is not configured for {self.name}")
        if not self.base_url.startswith(("http://", "https://")):
            raise ConfigurationError("Provider base URL must use HTTP or HTTPS")
        if self.timeout <= 0:
            raise ConfigurationError("Request timeout must be positive")
        if self.name == "zhipu" and not 0 < self.temperature <= 1:
            raise ConfigurationError(
                "AGENT_MODEL_TEMPERATURE must be greater than 0 and at most 1 "
                "for Zhipu GLM"
            )

    @classmethod
    def from_env(
        cls,
        provider: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> ProviderConfig:
        """Build provider settings without exposing secret values."""
        values = os.environ if environ is None else environ
        requested_name = (
            provider or values.get("AGENT_PROVIDER") or DEFAULT_PROVIDER_PRIORITY[0]
        ).casefold()
        name = PROVIDER_ALIASES.get(requested_name, requested_name)
        temperature = _float_setting(
            values,
            "AGENT_MODEL_TEMPERATURE",
            0.1,
        )
        timeout = _float_setting(values, "AGENT_REQUEST_TIMEOUT", 120.0)

        if name == "lmstudio":
            return cls(
                name=name,
                model=values.get("LM_STUDIO_MODEL", "local-model"),
                base_url=values.get(
                    "LM_STUDIO_BASE_URL",
                    "http://127.0.0.1:1234/v1",
                ).rstrip("/"),
                api_key=values.get("LM_STUDIO_API_KEY") or "lm-studio",
                temperature=temperature,
                timeout=timeout,
            )

        if name == "openai":
            model = values.get("OPENAI_MODEL", "gpt-5.6-sol")
            configured_effort = values.get("OPENAI_REASONING_EFFORT", "").strip()
            reasoning_effort = configured_effort or (
                "none" if model.casefold().startswith("gpt-5.6") else None
            )
            return cls(
                name=name,
                model=model,
                base_url=values.get(
                    "OPENAI_BASE_URL",
                    "https://api.openai.com/v1",
                ).rstrip("/"),
                api_key=_required_key(values, "OPENAI_API_KEY", name),
                temperature=temperature,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            )

        if name == "yandex":
            model_uri = values.get("YANDEX_MODEL_URI")
            folder_id = values.get("YANDEX_FOLDER_ID")
            if not model_uri and folder_id:
                model_uri = f"gpt://{folder_id}/yandexgpt/latest"
            if not model_uri:
                raise ConfigurationError(
                    "Set YANDEX_MODEL_URI or YANDEX_FOLDER_ID for YandexGPT"
                )
            return cls(
                name=name,
                model=model_uri,
                base_url=values.get(
                    "YANDEX_BASE_URL",
                    "https://ai.api.cloud.yandex.net/v1",
                ).rstrip("/"),
                api_key=_required_key(values, "YANDEX_API_KEY", name),
                temperature=temperature,
                timeout=timeout,
            )

        if name == "deepseek":
            return cls(
                name=name,
                model=values.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                base_url=values.get(
                    "DEEPSEEK_BASE_URL",
                    "https://api.deepseek.com",
                ).rstrip("/"),
                api_key=_required_key(values, "DEEPSEEK_API_KEY", name),
                temperature=temperature,
                timeout=timeout,
                extra_body={"thinking": {"type": "disabled"}},
            )

        if name == "qwen":
            return cls(
                name=name,
                model=values.get("QWEN_MODEL", "qwen3.7-plus"),
                base_url=values.get(
                    "QWEN_BASE_URL",
                    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                ).rstrip("/"),
                api_key=_required_key(values, "DASHSCOPE_API_KEY", name),
                temperature=temperature,
                timeout=timeout,
                extra_body={"enable_thinking": False},
            )

        if name == "zhipu":
            return cls(
                name=name,
                model=(
                    values.get("ZAI_MODEL") or values.get("ZHIPU_MODEL") or "glm-5.3"
                ),
                base_url=(
                    values.get("ZAI_BASE_URL")
                    or values.get("ZHIPU_BASE_URL")
                    or "https://api.z.ai/api/paas/v4"
                ).rstrip("/"),
                api_key=_required_key_with_alias(
                    values,
                    "ZAI_API_KEY",
                    "ZHIPU_API_KEY",
                    name,
                ),
                temperature=temperature,
                timeout=timeout,
                extra_body={"thinking": {"type": "enabled"}},
            )

        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise ConfigurationError(f"Unknown provider '{name}'. Supported: {supported}")

    @classmethod
    def priority_from_env(
        cls,
        provider: str | None = None,
        providers: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> tuple[ProviderConfig, ...]:
        """Resolve an ordered, duplicate-free provider failover chain."""

        if provider is not None and providers is not None:
            raise ConfigurationError("Use either --provider or --providers, not both")
        values = os.environ if environ is None else environ
        raw_priority = providers
        if raw_priority is None and provider is None:
            environment_priority = values.get("AGENT_PROVIDER_PRIORITY", "").strip()
            raw_priority = environment_priority or None
        if raw_priority is not None:
            requested = [item.strip() for item in raw_priority.split(",")]
            if not requested or any(not item for item in requested):
                raise ConfigurationError(
                    "Provider priority must be a comma-separated list without "
                    "empty entries"
                )
        elif provider is not None:
            requested = [provider]
        else:
            legacy_provider = values.get("AGENT_PROVIDER", "").strip()
            requested = (
                [legacy_provider]
                if legacy_provider
                else list(DEFAULT_PROVIDER_PRIORITY)
            )

        resolved: list[ProviderConfig] = []
        seen: set[str] = set()
        for requested_name in requested:
            config = cls.from_env(requested_name, values)
            if config.name in seen:
                raise ConfigurationError(
                    f"Provider '{config.name}' is repeated in the priority chain"
                )
            seen.add(config.name)
            resolved.append(config)
        return tuple(resolved)


def _required_key(
    environ: Mapping[str, str],
    variable_name: str,
    provider: str,
) -> str:
    value = environ.get(variable_name, "").strip()
    if not value:
        raise ConfigurationError(
            f"{variable_name} is required for provider '{provider}'"
        )
    return value


def _required_key_with_alias(
    environ: Mapping[str, str],
    variable_name: str,
    alias: str,
    provider: str,
) -> str:
    value = (environ.get(variable_name) or environ.get(alias) or "").strip()
    if not value:
        raise ConfigurationError(
            f"{variable_name} or {alias} is required for provider '{provider}'"
        )
    return value


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Resolved local paths and retrieval settings."""

    project_root: Path
    workspace: Path
    data_dir: Path
    context_root: Path
    context_top_k: int = 8
    auto_context_max_chars: int = 12_000
    auto_context_query_max_chars: int = 2_000
    chunk_size: int = 4_000
    chunk_overlap: int = 400
    max_file_bytes: int = 2 * 1024 * 1024 * 1024
    model_call_retries: int = 3
    model_retry_initial_delay: float = 1.0
    model_retry_max_delay: float = 15.0
    web_retry_attempts: int = 3
    recursion_limit: int = 100
    audit_batch_size: int = 8
    audit_max_batches_per_request: int = 4
    audit_max_reads_per_file: int = 4
    audit_include: tuple[str, ...] = ()
    audit_exclude: tuple[str, ...] = ()
    project_check_timeout_seconds: int = 300
    project_check_output_max_chars: int = 20_000

    def __post_init__(self) -> None:
        if self.context_top_k <= 0:
            raise ConfigurationError("AGENT_CONTEXT_TOP_K must be positive")
        if self.auto_context_max_chars <= 0:
            raise ConfigurationError("AGENT_AUTO_CONTEXT_MAX_CHARS must be positive")
        if self.auto_context_query_max_chars <= 0:
            raise ConfigurationError(
                "AGENT_AUTO_CONTEXT_QUERY_MAX_CHARS must be positive"
            )
        if self.chunk_size <= 0:
            raise ConfigurationError("AGENT_CONTEXT_CHUNK_SIZE must be positive")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ConfigurationError(
                "Context chunk overlap must be non-negative and smaller than chunk size"
            )
        if self.max_file_bytes <= 0:
            raise ConfigurationError("AGENT_CONTEXT_MAX_FILE_MB must be positive")
        if self.model_call_retries < 0:
            raise ConfigurationError("AGENT_MODEL_CALL_RETRIES cannot be negative")
        if self.model_retry_initial_delay < 0:
            raise ConfigurationError(
                "AGENT_MODEL_RETRY_INITIAL_DELAY cannot be negative"
            )
        if self.model_retry_max_delay < self.model_retry_initial_delay:
            raise ConfigurationError(
                "AGENT_MODEL_RETRY_MAX_DELAY must be at least the initial delay"
            )
        if self.web_retry_attempts <= 0:
            raise ConfigurationError("AGENT_WEB_RETRY_ATTEMPTS must be positive")
        if not 25 <= self.recursion_limit <= 500:
            raise ConfigurationError("AGENT_RECURSION_LIMIT must be between 25 and 500")
        if not 1 <= self.audit_batch_size <= 25:
            raise ConfigurationError("AGENT_AUDIT_BATCH_SIZE must be between 1 and 25")
        if not 1 <= self.audit_max_batches_per_request <= 100:
            raise ConfigurationError(
                "AGENT_AUDIT_MAX_BATCHES_PER_REQUEST must be between 1 and 100"
            )
        if not 2 <= self.audit_max_reads_per_file <= 12:
            raise ConfigurationError(
                "AGENT_AUDIT_MAX_READS_PER_FILE must be between 2 and 12"
            )
        for name, patterns in (
            ("AGENT_AUDIT_INCLUDE", self.audit_include),
            ("AGENT_AUDIT_EXCLUDE", self.audit_exclude),
        ):
            if len(patterns) > 100 or any(len(pattern) > 500 for pattern in patterns):
                raise ConfigurationError(
                    f"{name} supports at most 100 patterns of 500 characters"
                )
        if not 10 <= self.project_check_timeout_seconds <= 3_600:
            raise ConfigurationError(
                "AGENT_PROJECT_CHECK_TIMEOUT_SECONDS must be between 10 and 3600"
            )
        if not 1_000 <= self.project_check_output_max_chars <= 100_000:
            raise ConfigurationError(
                "AGENT_PROJECT_CHECK_OUTPUT_MAX_CHARS must be between 1000 and 100000"
            )

    @property
    def context_database(self) -> Path:
        """Return the path of the persistent context database."""
        return self.data_dir / "context.sqlite3"

    @property
    def checkpoint_database(self) -> Path:
        """Return the path of the LangGraph checkpoint database."""
        return self.data_dir / "checkpoints.sqlite3"

    @property
    def project_audit_database(self) -> Path:
        """Return the persistent project-audit manifest database path."""
        return self.data_dir / "project_audit.sqlite3"

    @classmethod
    def from_env(
        cls,
        base_dir: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> AppConfig:
        """Resolve application paths relative to the selected project directory."""
        values = os.environ if environ is None else environ
        root = (base_dir or Path.cwd()).resolve()
        return cls(
            project_root=root,
            workspace=_absolute_path(
                values.get("AGENT_WORKSPACE", "./agent_workspace"),
                root,
            ),
            data_dir=_absolute_path(
                values.get("AGENT_DATA_DIR", "./.agent_data"),
                root,
            ),
            context_root=_absolute_path(
                values.get("AGENT_CONTEXT_ROOT", "./agent_workspace"),
                root,
            ),
            context_top_k=_int_setting_with_alias(
                values,
                "AGENT_CONTEXT_TOP_K",
                "AGENT_RETRIEVAL_LIMIT",
                8,
            ),
            auto_context_max_chars=_int_setting(
                values,
                "AGENT_AUTO_CONTEXT_MAX_CHARS",
                12_000,
            ),
            auto_context_query_max_chars=_int_setting(
                values,
                "AGENT_AUTO_CONTEXT_QUERY_MAX_CHARS",
                2_000,
            ),
            chunk_size=_int_setting(values, "AGENT_CONTEXT_CHUNK_SIZE", 4_000),
            chunk_overlap=_int_setting(
                values,
                "AGENT_CONTEXT_CHUNK_OVERLAP",
                400,
            ),
            max_file_bytes=(
                _int_setting(values, "AGENT_CONTEXT_MAX_FILE_MB", 2_048) * 1024 * 1024
            ),
            model_call_retries=_int_setting(
                values,
                "AGENT_MODEL_CALL_RETRIES",
                3,
            ),
            model_retry_initial_delay=_float_setting(
                values,
                "AGENT_MODEL_RETRY_INITIAL_DELAY",
                1.0,
            ),
            model_retry_max_delay=_float_setting(
                values,
                "AGENT_MODEL_RETRY_MAX_DELAY",
                15.0,
            ),
            web_retry_attempts=_int_setting(
                values,
                "AGENT_WEB_RETRY_ATTEMPTS",
                3,
            ),
            recursion_limit=_int_setting(
                values,
                "AGENT_RECURSION_LIMIT",
                100,
            ),
            audit_batch_size=_int_setting(
                values,
                "AGENT_AUDIT_BATCH_SIZE",
                8,
            ),
            audit_max_batches_per_request=_int_setting(
                values,
                "AGENT_AUDIT_MAX_BATCHES_PER_REQUEST",
                4,
            ),
            audit_max_reads_per_file=_int_setting(
                values,
                "AGENT_AUDIT_MAX_READS_PER_FILE",
                4,
            ),
            audit_include=_pattern_setting(values, "AGENT_AUDIT_INCLUDE"),
            audit_exclude=_pattern_setting(values, "AGENT_AUDIT_EXCLUDE"),
            project_check_timeout_seconds=_int_setting(
                values,
                "AGENT_PROJECT_CHECK_TIMEOUT_SECONDS",
                300,
            ),
            project_check_output_max_chars=_int_setting(
                values,
                "AGENT_PROJECT_CHECK_OUTPUT_MAX_CHARS",
                20_000,
            ),
        )

    def prepare_directories(self) -> None:
        """Create application-owned directories if they do not exist."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.context_root.mkdir(parents=True, exist_ok=True)
