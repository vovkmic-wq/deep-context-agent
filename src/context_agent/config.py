"""Environment-driven configuration for the application and LLM providers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from context_agent.errors import ConfigurationError

SUPPORTED_PROVIDERS = ("lmstudio", "openai", "yandex", "deepseek", "qwen")


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


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Resolved settings for one OpenAI-compatible chat model."""

    name: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    temperature: float = 0.1
    timeout: float = 120.0
    max_retries: int = 2
    extra_body: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name not in SUPPORTED_PROVIDERS:
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
        if self.max_retries < 0:
            raise ConfigurationError("Max retries cannot be negative")

    @classmethod
    def from_env(
        cls,
        provider: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> ProviderConfig:
        """Build provider settings without exposing secret values."""
        values = os.environ if environ is None else environ
        name = (provider or values.get("AGENT_PROVIDER", "lmstudio")).casefold()
        temperature = _float_setting(
            values,
            "AGENT_MODEL_TEMPERATURE",
            0.1,
        )
        timeout = _float_setting(values, "AGENT_REQUEST_TIMEOUT", 120.0)
        max_retries = _int_setting(values, "AGENT_MAX_RETRIES", 2)

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
                max_retries=max_retries,
            )

        if name == "openai":
            return cls(
                name=name,
                model=values.get("OPENAI_MODEL", "gpt-5.5"),
                base_url=values.get(
                    "OPENAI_BASE_URL",
                    "https://api.openai.com/v1",
                ).rstrip("/"),
                api_key=_required_key(values, "OPENAI_API_KEY", name),
                temperature=temperature,
                timeout=timeout,
                max_retries=max_retries,
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
                max_retries=max_retries,
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
                max_retries=max_retries,
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
                max_retries=max_retries,
                extra_body={"enable_thinking": False},
            )

        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise ConfigurationError(f"Unknown provider '{name}'. Supported: {supported}")


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


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Resolved local paths and retrieval settings."""

    project_root: Path
    workspace: Path
    data_dir: Path
    context_root: Path
    context_top_k: int = 8
    chunk_size: int = 4_000
    chunk_overlap: int = 400
    max_file_bytes: int = 2 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.context_top_k <= 0:
            raise ConfigurationError("AGENT_CONTEXT_TOP_K must be positive")
        if self.chunk_size <= 0:
            raise ConfigurationError("AGENT_CONTEXT_CHUNK_SIZE must be positive")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ConfigurationError(
                "Context chunk overlap must be non-negative and smaller than chunk size"
            )
        if self.max_file_bytes <= 0:
            raise ConfigurationError("AGENT_CONTEXT_MAX_FILE_MB must be positive")

    @property
    def context_database(self) -> Path:
        """Return the path of the persistent context database."""
        return self.data_dir / "context.sqlite3"

    @property
    def checkpoint_database(self) -> Path:
        """Return the path of the LangGraph checkpoint database."""
        return self.data_dir / "checkpoints.sqlite3"

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
            context_top_k=_int_setting(values, "AGENT_CONTEXT_TOP_K", 8),
            chunk_size=_int_setting(values, "AGENT_CONTEXT_CHUNK_SIZE", 4_000),
            chunk_overlap=_int_setting(
                values,
                "AGENT_CONTEXT_CHUNK_OVERLAP",
                400,
            ),
            max_file_bytes=(
                _int_setting(values, "AGENT_CONTEXT_MAX_FILE_MB", 2_048) * 1024 * 1024
            ),
        )

    def prepare_directories(self) -> None:
        """Create application-owned directories if they do not exist."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.context_root.mkdir(parents=True, exist_ok=True)
