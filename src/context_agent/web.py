"""Secure local FastAPI interface backed by the CLI runtime and SQLite stores."""

# ruff: noqa: RUF001 -- bilingual Russian/English UI strings are intentional.

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import queue
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict, Field

from context_agent import __version__
from context_agent.autopilot import AutopilotProgress, AutopilotStore
from context_agent.config import AppConfig, ProviderConfig
from context_agent.context_store import ContextStore
from context_agent.diagnostics import (
    DiagnosticStore,
    classify_failure,
    configured_secret_values,
)
from context_agent.errors import (
    AgentError,
    ConfigurationError,
    ContextStoreError,
    PathSecurityError,
)
from context_agent.paths import resolve_inside
from context_agent.project_audit import AuditProgress, ProjectAuditStore
from context_agent.providers import create_chat_model
from context_agent.runtime import (
    AgentRuntime,
    is_long_running_project_request,
    message_text,
)
from context_agent.structured_logging import configure_structured_logger

_STATIC_ROOT = Path(__file__).parent / "static"
_STATE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SECRET_NAMES = frozenset(
    {
        ".agent_data",
        ".env",
        ".env.local",
        ".env.production",
        ".env.test",
        "context-agent-server.jsonl",
        "credentials.json",
        "diagnostics.sqlite3",
        "diagnostics.sqlite3-shm",
        "diagnostics.sqlite3-wal",
        "autopilot.sqlite3",
        "autopilot.sqlite3-shm",
        "autopilot.sqlite3-wal",
    }
)
_SETTINGS = {
    "context_top_k": {
        "environment": "AGENT_CONTEXT_TOP_K",
        "label": "Результаты поиска / Context results",
        "comment": "Сколько наиболее подходящих фрагментов возвращать из SQLite.",
        "minimum": 1,
        "maximum": 100,
    },
    "auto_context_max_chars": {
        "environment": "AGENT_AUTO_CONTEXT_MAX_CHARS",
        "label": "Лимит автоконтекста / Auto-context limit",
        "comment": "Максимум символов найденного контекста в одном запросе к LLM.",
        "minimum": 1_000,
        "maximum": 200_000,
    },
    "active_context_max_tokens": {
        "environment": "AGENT_ACTIVE_CONTEXT_MAX_TOKENS",
        "label": "Активное окно LLM / Active LLM window",
        "comment": (
            "После этого примерного числа токенов старые тела результатов "
            "инструментов заменяются компактными маркерами только в запросе к LLM; "
            "полная SQLite-история сохраняется."
        ),
        "minimum": 16_000,
        "maximum": 500_000,
    },
    "audit_batch_size": {
        "environment": "AGENT_AUDIT_BATCH_SIZE",
        "label": "Файлов в пакете / Files per batch",
        "comment": "Сколько файлов аудита анализировать за один ограниченный шаг LLM.",
        "minimum": 1,
        "maximum": 25,
    },
    "audit_max_batches_per_request": {
        "environment": "AGENT_AUDIT_MAX_BATCHES_PER_REQUEST",
        "label": "Пакетов за запуск / Batches per run",
        "comment": "Сколько пакетов обработать до безопасной точки продолжения.",
        "minimum": 1,
        "maximum": 100,
    },
    "audit_max_reads_per_file": {
        "environment": "AGENT_AUDIT_MAX_READS_PER_FILE",
        "label": "Чтений файла / Reads per file",
        "comment": "Защитный предел повторных чтений одного файла во время аудита.",
        "minimum": 2,
        "maximum": 12,
    },
}
_WORK_MODES = {
    "general": "Работай как универсальный инженер программного обеспечения.",
    "audit": "Проведи доказательный аудит кода и сначала сформулируй находки.",
    "coder": "Реализуй запрошенное изменение безопасно и минимально.",
    "tester": "Спроектируй и выполни тесты, не заявляя PASS без фактического лога.",
    "reviewer": "Выполни code review с приоритетом корректности и рисков.",
    "debugger": "Диагностируй первопричину ошибки и проверь исправление.",
    "refactor": "Улучши структуру без изменения наблюдаемого поведения.",
    "security": "Проверь границы доверия, секреты, пути и опасные операции.",
    "docs": "Обнови техническую документацию по фактическому поведению кода.",
    "architect": "Спроектируй изменение с учётом совместимости и эксплуатации.",
}
WorkMode = Literal[
    "general",
    "audit",
    "coder",
    "tester",
    "reviewer",
    "debugger",
    "refactor",
    "security",
    "docs",
    "architect",
]


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    thread_id: str = Field(default="web", min_length=1, max_length=100)
    auto_context: bool = True
    mode: WorkMode = "general"
    allow_write: bool = False


class AuditRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    thread_id: str = Field(default="web-audit", min_length=1, max_length=100)
    allow_write: bool = False
    max_batches: int | None = Field(default=None, ge=1, le=100)
    batch_size: int | None = Field(default=None, ge=1, le=25)
    include_patterns: list[str] = Field(default_factory=list, max_length=100)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=100)


class JobRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    thread_id: str = Field(default="web-job", min_length=1, max_length=100)
    allow_write: bool = False
    include_patterns: list[str] = Field(default_factory=list, max_length=100)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=100)


class IndexRequest(BaseModel):
    path: str = Field(default="/workspace", max_length=2_000)


class ThreadRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)


class FileWriteRequest(BaseModel):
    content: str = Field(max_length=2 * 1024 * 1024)
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class FileDeleteRequest(BaseModel):
    confirm_path: str = Field(min_length=1, max_length=2_000)


class ProviderDoctorRequest(BaseModel):
    live: bool = False


class ProviderPriorityRequest(BaseModel):
    providers: list[str] = Field(min_length=1, max_length=20)


class ProviderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^custom-[a-z0-9][a-z0-9-]{0,47}$")
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=8, max_length=500)


class SettingsRequest(BaseModel):
    values: dict[str, int | float] = Field(default_factory=dict)


class DiagnosticPurgeRequest(BaseModel):
    confirm: Literal["PURGE"]
    request_id: str | None = Field(default=None, min_length=1, max_length=100)
    older_than_days: int | None = Field(default=None, ge=0, le=3_650)


class _TaskCancelledError(Exception):
    pass


class _PublicTaskError(Exception):
    """Expected background failure with a bounded user-facing explanation."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


@dataclass(slots=True)
class _Task:
    task_id: str
    kind: str
    request_id: str | None = None
    events: queue.Queue[dict[str, object]] = field(default_factory=queue.Queue)
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    terminal_event: dict[str, object] | None = None


_AGENT_FAILURE_MESSAGES = {
    "context_window_exceeded": (
        "Активное окно модели переполнено. Старые результаты инструментов будут "
        "автоматически компактированы; повторите запрос."
    ),
    "quota_exhausted": (
        "У удалённых провайдеров закончились доступные средства или квота. "
        "Проверьте биллинг либо включите локального провайдера."
    ),
    "rate_limited": (
        "Провайдер временно ограничил частоту запросов. Подождите и повторите запрос."
    ),
    "authentication_failed": (
        "Провайдер отклонил учётные данные. Проверьте серверную настройку API-ключа."
    ),
    "provider_timeout": (
        "Провайдер не ответил за допустимое время. Проверьте live-статус и повторите."
    ),
    "provider_unavailable": (
        "Провайдер недоступен или разорвал соединение. Проверьте live-статус и сеть."
    ),
    "agent_step_limit": (
        "Агент достиг безопасного лимита шагов. Разделите задачу на меньшие этапы."
    ),
    "provider_chain_failed": (
        "LLM-запрос не завершён ни одним провайдером. Проверьте их live-статус "
        "на вкладке «Провайдеры» и повторите запрос."
    ),
}
_RETRYABLE_AGENT_FAILURES = frozenset(
    {
        "context_window_exceeded",
        "rate_limited",
        "provider_timeout",
        "provider_unavailable",
    }
)


def _agent_failure_code(exc: BaseException) -> str:
    """Compatibility wrapper around the shared safe classifier."""

    return classify_failure(exc)


def _is_benign_windows_pipe_reset(context: Mapping[str, object]) -> bool:
    """Identify the Proactor callback emitted for a closed browser/SSE socket."""

    if os.name != "nt":
        return False
    exception = context.get("exception")
    if not isinstance(exception, ConnectionResetError):
        return False
    if getattr(exception, "winerror", None) != 10054:
        return False
    diagnostic = " ".join(
        str(context.get(key, "")) for key in ("message", "handle", "future")
    )
    return "_ProactorBasePipeTransport._call_connection_lost" in diagnostic


class TaskRegistry:
    """Run bounded background jobs and expose sanitized event streams."""

    def __init__(
        self,
        diagnostic_store: DiagnosticStore,
        logger: logging.Logger,
        max_workers: int = 4,
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="context-agent-web",
        )
        self._tasks: dict[str, _Task] = {}
        self._lock = threading.RLock()
        self._diagnostic_store = diagnostic_store
        self._logger = logger

    def submit(
        self,
        kind: str,
        operation: Callable[
            [Callable[[str, Mapping[str, object]], None], threading.Event], object
        ],
        *,
        task_id: str | None = None,
        request_id: str | None = None,
    ) -> str:
        task = _Task(
            task_id=task_id or uuid4().hex,
            kind=kind,
            request_id=request_id,
        )
        self._diagnostic_store.record_task_start(
            task.task_id,
            kind,
            request_id=request_id,
        )
        with self._lock:
            if len(self._tasks) >= 1_000:
                completed = [
                    task_id
                    for task_id, existing in self._tasks.items()
                    if existing.done.is_set()
                ]
                for task_id in completed[:500]:
                    self._tasks.pop(task_id, None)
            self._tasks[task.task_id] = task

        def emit(event: str, data: Mapping[str, object]) -> None:
            safe_data = dict(data)
            if event in {"completed", "cancelled", "failed"}:
                safe_data.setdefault("task_id", task.task_id)
                safe_data.setdefault("request_id", task.request_id or task.task_id)
                safe_data.setdefault("retryable", False)
            record: dict[str, object] = {"event": event, "data": safe_data}
            if event in {"completed", "cancelled", "failed"}:
                task.terminal_event = record
                self._diagnostic_store.record_task_terminal(task.task_id, record)
            task.events.put(record)

        def runner() -> None:
            emit("started", {"task_id": task.task_id, "kind": kind})
            try:
                if task.cancel.is_set():
                    raise _TaskCancelledError
                result = operation(emit, task.cancel)
                if task.cancel.is_set():
                    raise _TaskCancelledError
                emit("result", {"result": result})
                emit("completed", {"task_id": task.task_id})
            except _TaskCancelledError:
                emit("cancelled", {"task_id": task.task_id})
            except _PublicTaskError as exc:
                emit(
                    "failed",
                    {
                        "task_id": task.task_id,
                        "request_id": task.request_id or task.task_id,
                        "error_type": exc.code,
                        "message": exc.safe_message,
                        "retryable": exc.retryable,
                    },
                )
            except AgentError as exc:
                error_code = _agent_failure_code(exc)
                self._logger.error(
                    "Web background task failed",
                    extra={
                        "event_code": error_code,
                        "safe_fields": {
                            "task_id": task.task_id,
                            "kind": kind,
                            "exception_type": type(exc).__name__,
                        },
                    },
                )
                emit(
                    "failed",
                    {
                        "task_id": task.task_id,
                        "request_id": task.request_id or task.task_id,
                        "error_type": error_code,
                        "message": _AGENT_FAILURE_MESSAGES[error_code],
                        "retryable": error_code in _RETRYABLE_AGENT_FAILURES,
                    },
                )
            except Exception as exc:  # backend boundary: never expose raw details
                self._logger.error(
                    "Unhandled Web background task failure",
                    extra={
                        "event_code": "background_task_failed",
                        "safe_fields": {
                            "task_id": task.task_id,
                            "kind": kind,
                            "exception_type": type(exc).__name__,
                        },
                    },
                )
                emit(
                    "failed",
                    {
                        "task_id": task.task_id,
                        "request_id": task.request_id or task.task_id,
                        "error_type": "background_task_failed",
                        "message": (
                            "Операция завершилась ошибкой. Проверьте журнал сервера."
                        ),
                        "retryable": False,
                    },
                )
            finally:
                task.done.set()

        self._executor.submit(runner)
        return task.task_id

    def get(self, task_id: str) -> _Task:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            persisted = self._diagnostic_store.task(task_id)
            if persisted is None:
                raise KeyError(task_id)
            task = _Task(
                task_id=task_id,
                kind=str(persisted["kind"]),
                request_id=(
                    str(persisted["request_id"])
                    if persisted.get("request_id")
                    else None
                ),
            )
            terminal = persisted.get("terminal_event")
            if isinstance(terminal, dict):
                task.terminal_event = terminal
            task.done.set()
        return task

    def cancel(self, task_id: str) -> None:
        self.get(task_id).cancel.set()

    def active_count(self) -> int:
        with self._lock:
            return sum(not task.done.is_set() for task in self._tasks.values())

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


class ProviderRegistry:
    """Thread-safe live provider order used by every Web operation."""

    def __init__(self, providers: tuple[ProviderConfig, ...]) -> None:
        if not providers:
            raise ValueError("At least one provider is required")
        self._providers = providers
        self._custom: dict[str, ProviderConfig] = {
            item.name: item for item in providers if item.name.startswith("custom-")
        }
        self._lock = threading.RLock()

    def snapshot(self) -> tuple[ProviderConfig, ...]:
        """Return one immutable provider-chain snapshot for an operation."""

        with self._lock:
            return self._providers

    def replace(self, requested: list[str]) -> tuple[ProviderConfig, ...]:
        """Validate and atomically activate an ordered provider chain."""

        normalized = [item.strip().casefold() for item in requested]
        if any(not item for item in normalized):
            raise ConfigurationError("Provider names cannot be empty")
        active = {item.name: item for item in self.snapshot()}
        with self._lock:
            custom = dict(self._custom)
        resolved: list[ProviderConfig] = []
        seen: set[str] = set()
        for requested_name in normalized:
            canonical = "zhipu" if requested_name == "glm" else requested_name
            if canonical in seen:
                raise ConfigurationError(f"Provider '{canonical}' is repeated")
            provider = active.get(canonical) or custom.get(canonical)
            if provider is None:
                provider = ProviderConfig.from_env(canonical)
            seen.add(canonical)
            resolved.append(provider)
        updated = tuple(resolved)
        with self._lock:
            self._providers = updated
        return updated

    def add_custom(self, provider: ProviderConfig) -> None:
        """Add one validated custom provider to the process-local catalog."""

        if not provider.name.startswith("custom-"):
            raise ConfigurationError("Custom provider ID must start with custom-")
        with self._lock:
            if provider.name in self._custom or any(
                item.name == provider.name for item in self._providers
            ):
                raise ConfigurationError("Provider ID already exists")
            self._custom[provider.name] = provider

    def get(self, name: str) -> ProviderConfig | None:
        """Return configured provider metadata without exposing its credential."""

        canonical = "zhipu" if name.casefold() == "glm" else name.casefold()
        with self._lock:
            for provider in self._providers:
                if provider.name == canonical:
                    return provider
            return self._custom.get(canonical)

    def update(self, provider: ProviderConfig) -> None:
        """Replace one provider config in the catalog and active chain."""

        with self._lock:
            if provider.name.startswith("custom-"):
                self._custom[provider.name] = provider
            self._providers = tuple(
                provider if item.name == provider.name else item
                for item in self._providers
            )

    def catalog(self) -> list[dict[str, object]]:
        """Return configured/active metadata without returning credentials."""

        active = self.snapshot()
        active_by_name = {
            item.name: (position, item) for position, item in enumerate(active)
        }
        items: list[dict[str, object]] = []
        with self._lock:
            custom_names = tuple(sorted(self._custom))
        names = (
            "lmstudio",
            "zhipu",
            "openai",
            "yandex",
            "deepseek",
            "qwen",
            *custom_names,
        )
        for name in names:
            active_entry = active_by_name.get(name)
            provider = active_entry[1] if active_entry else None
            if provider is None:
                with self._lock:
                    provider = self._custom.get(name)
            configured = provider is not None
            configuration_error = ""
            if provider is None and not name.startswith("custom-"):
                try:
                    provider = ProviderConfig.from_env(name)
                    configured = True
                except ConfigurationError:
                    configuration_error = "Требуется настройка модели или API-ключа"
            items.append(
                {
                    "provider": name,
                    "model": provider.model if provider else "",
                    "base_url": provider.base_url if provider else "",
                    "api_key": "configured" if configured else "missing",
                    "configured": configured,
                    "active": active_entry is not None,
                    "priority": active_entry[0] if active_entry else None,
                    "configuration_error": configuration_error,
                    "local": bool(
                        provider and _is_loopback_base_url(provider.base_url)
                    ),
                    "custom": name.startswith("custom-"),
                }
            )
        return items


class _RemoteRateLimiter:
    """Small in-memory fixed-window guard for explicitly enabled remote mode."""

    def __init__(self, limit: int = 60, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allows(self, client: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            requests = self._requests[client]
            while requests and requests[0] < cutoff:
                requests.popleft()
            if len(requests) >= self._limit:
                return False
            requests.append(now)
            return True


def _request_payload(request: Request, **values: object) -> dict[str, object]:
    return {"request_id": request.state.request_id, **values}


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request.state.request_id,
                "retryable": retryable,
            }
        },
    )


def _is_secret_path(path: Path) -> bool:
    return any(
        part.casefold() in _SECRET_NAMES
        or part.casefold().startswith((".env.", "id_rsa", "id_ed25519"))
        for part in path.parts
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_loopback_base_url(base_url: str) -> bool:
    """Return whether an OpenAI-compatible endpoint is strictly local."""

    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").casefold()
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _custom_api_key_environment(provider_name: str) -> str:
    suffix = re.sub(r"[^A-Z0-9]", "_", provider_name.upper())
    return f"{suffix}_API_KEY"


def _custom_provider(body: ProviderCreateRequest) -> ProviderConfig:
    """Build a safe custom OpenAI-compatible provider without browser secrets."""

    parsed = urlparse(body.base_url.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError("Provider URL is invalid")
    base_url = body.base_url.strip().rstrip("/")
    local = _is_loopback_base_url(base_url)
    if parsed.scheme == "http" and not local:
        raise ConfigurationError("Remote custom providers require HTTPS")
    environment = _custom_api_key_environment(body.name)
    api_key = "local-provider" if local else os.getenv(environment, "").strip()
    if not api_key:
        raise ConfigurationError(f"Set server environment variable {environment}")
    return ProviderConfig(
        name=body.name,
        model=body.model.strip(),
        base_url=base_url,
        api_key=api_key,
    )


def _probe_openai_models(provider: ProviderConfig) -> tuple[str, ...]:
    """Read a bounded OpenAI-compatible model catalog with safe failures."""

    endpoint = provider.base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if provider.api_key not in {"lm-studio", "local-provider"}:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    request = UrlRequest(endpoint, headers=headers)
    try:
        with urlopen(request, timeout=min(provider.timeout, 10.0)) as response:
            raw = response.read(1024 * 1024 + 1)
    except HTTPError as exc:
        message = (
            "Сервер отклонил API-ключ провайдера."
            if exc.code in {401, 403}
            else "Сервер провайдера ответил ошибкой на запрос списка моделей."
        )
        raise _PublicTaskError("provider_catalog_failed", message) from exc
    except (OSError, TimeoutError, URLError) as exc:
        raise _PublicTaskError(
            "provider_unreachable",
            "Локальный сервер модели недоступен. Запустите сервер и загрузите модель.",
        ) from exc
    if len(raw) > 1024 * 1024:
        raise _PublicTaskError(
            "provider_catalog_too_large",
            "Список моделей провайдера превысил безопасный размер.",
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        models = tuple(
            str(row["id"])
            for row in rows[:100]
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        )
    except (UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise _PublicTaskError(
            "provider_catalog_invalid",
            "Сервер вернул некорректный список моделей.",
        ) from exc
    if not models:
        raise _PublicTaskError(
            "provider_model_missing",
            "Сервер доступен, но ни одна модель не загружена.",
        )
    return models


def _runtime_factory(
    config: AppConfig,
    providers: tuple[ProviderConfig, ...],
) -> AgentRuntime:
    return AgentRuntime(
        config,
        providers[0],
        fallback_provider_configs=providers[1:],
    )


def create_app(
    config: AppConfig,
    providers: tuple[ProviderConfig, ...],
    *,
    allow_remote: bool = False,
    auth_token: str | None = None,
    trusted_https_proxy: bool = False,
) -> FastAPI:
    """Create the local web application with same-origin security defaults."""

    config.prepare_directories()
    if allow_remote and not auth_token:
        raise ValueError("Remote web mode requires AGENT_WEB_AUTH_TOKEN")
    if allow_remote and not trusted_https_proxy:
        raise ValueError("Remote web mode requires a trusted HTTPS reverse proxy")
    if not _STATIC_ROOT.joinpath("index.html").is_file():
        raise RuntimeError("Web static bundle is missing")

    diagnostics = DiagnosticStore(
        config.diagnostics_database,
        mode=cast(Any, config.failure_log_mode),
        retention_days=config.failure_log_retention_days,
        max_rows=config.failure_log_max_rows,
        query_max_bytes=config.failure_log_query_max_bytes,
        known_secrets=(
            *(provider.api_key for provider in providers),
            *configured_secret_values(),
        ),
    )
    diagnostics.recover_interrupted()
    logger = configure_structured_logger(
        config.data_dir,
        known_secrets=(
            *(provider.api_key for provider in providers),
            *configured_secret_values(),
        ),
    )
    tasks = TaskRegistry(diagnostics, logger)
    provider_registry = ProviderRegistry(providers)
    rate_limiter = _RemoteRateLimiter()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        def exception_handler(
            active_loop: asyncio.AbstractEventLoop,
            context: dict[str, Any],
        ) -> None:
            if _is_benign_windows_pipe_reset(context):
                return
            exception = context.get("exception")
            logger.error(
                "Unhandled asyncio event-loop exception",
                extra={
                    "event_code": "asyncio_loop_error",
                    "safe_fields": {
                        "exception_type": (
                            type(exception).__name__
                            if isinstance(exception, BaseException)
                            else "unknown"
                        )
                    },
                },
            )
            if previous_handler is not None:
                previous_handler(active_loop, context)
            else:
                active_loop.default_exception_handler(context)

        loop.set_exception_handler(exception_handler)
        try:
            yield
        finally:
            loop.set_exception_handler(previous_handler)
            tasks.close()
            diagnostics.close()

    app = FastAPI(
        title="Deep Context Agent",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.tasks = tasks
    app.state.config = config
    app.state.providers = provider_registry
    app.state.diagnostics = diagnostics

    @app.middleware("http")
    async def security_boundary(request: Request, call_next: Callable[..., Any]):
        request.state.request_id = uuid4().hex
        csrf_token = request.cookies.get("dca_csrf") or secrets.token_urlsafe(32)
        request.state.csrf_token = csrf_token
        if allow_remote:
            client = request.client.host if request.client else "unknown"
            if not rate_limiter.allows(client):
                return _error_response(
                    request,
                    429,
                    "rate_limit_exceeded",
                    "Слишком много запросов. Повторите позже.",
                    retryable=True,
                )
            authorization = request.headers.get("authorization", "")
            expected = f"Bearer {auth_token}"
            if not secrets.compare_digest(authorization, expected):
                return _error_response(
                    request,
                    401,
                    "authentication_required",
                    "Требуется корректный bearer token.",
                )
        if request.method in _STATE_METHODS:
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if origin and origin not in {f"http://{host}", f"https://{host}"}:
                return _error_response(
                    request,
                    403,
                    "origin_denied",
                    "Источник запроса не разрешён.",
                )
            submitted = request.headers.get("x-csrf-token", "")
            if not submitted or not secrets.compare_digest(submitted, csrf_token):
                return _error_response(
                    request,
                    403,
                    "csrf_failed",
                    "CSRF token отсутствует или недействителен.",
                )
        response = await call_next(request)
        if "dca_csrf" not in request.cookies:
            response.set_cookie(
                "dca_csrf",
                csrf_token,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
            )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request rejected"
        return _error_response(
            request,
            exc.status_code,
            f"http_{exc.status_code}",
            detail,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request,
            422,
            "validation_error",
            "Параметры запроса не прошли проверку.",
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled Web request failure",
            extra={
                "event_code": "web_request_failed",
                "safe_fields": {
                    "request_id": request.state.request_id,
                    "exception_type": type(exc).__name__,
                },
            },
        )
        return _error_response(
            request,
            500,
            "internal_error",
            "Внутренняя ошибка сервера.",
            retryable=True,
        )

    @app.get("/api/health")
    def health(request: Request) -> dict[str, object]:
        database_ok = True
        try:
            with ContextStore(config.context_database) as store:
                store.list_sources(limit=1)
            with ProjectAuditStore(config.project_audit_database) as store:
                store.list_runs(workspace=config.workspace, limit=1)
            diagnostics.list_requests(limit=1)
        except Exception:
            database_ok = False
        return _request_payload(
            request,
            status="ok" if database_ok else "degraded",
            version=__version__,
            database=database_ok,
            static_bundle=True,
        )

    @app.get("/api/runtime")
    def runtime_info(request: Request) -> dict[str, object]:
        current_providers = provider_registry.snapshot()
        return _request_payload(
            request,
            version=__version__,
            csrf_token=request.state.csrf_token,
            provider=current_providers[0].name,
            model=current_providers[0].model,
            provider_priority=[item.name for item in current_providers],
            workspace="/workspace",
            active_tasks=tasks.active_count(),
            audit_mode_default="read-only",
            work_modes=list(_WORK_MODES),
            failure_log_mode=config.failure_log_mode,
        )

    @app.get("/api/threads")
    def list_threads(request: Request, limit: int = Query(100, ge=1, le=500)):
        with ContextStore(config.context_database) as store:
            items = store.list_threads(limit=limit)
        return _request_payload(request, items=items)

    @app.post("/api/threads")
    def create_thread(request: Request, body: ThreadRequest):
        safe = body.thread_id.strip()
        if not safe:
            raise HTTPException(422, "Thread ID cannot be empty")
        return _request_payload(request, thread_id=safe)

    @app.get("/api/threads/{thread_id}/messages")
    def thread_messages(
        request: Request,
        thread_id: str,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        with ContextStore(config.context_database) as store:
            items = store.thread_messages(thread_id, limit=limit, offset=offset)
        return _request_payload(request, items=items, limit=limit, offset=offset)

    @app.post("/api/chat", status_code=202)
    def chat(request: Request, body: ChatRequest):
        task_id = uuid4().hex

        def operation(
            emit: Callable[[str, Mapping[str, object]], None],
            cancelled: threading.Event,
        ) -> object:
            current_providers = provider_registry.snapshot()
            with _runtime_factory(config, current_providers) as runtime:
                if cancelled.is_set():
                    raise _TaskCancelledError
                mode_instruction = _WORK_MODES[body.mode]
                query = f"Режим работы: {body.mode}. {mode_instruction}\n\n{body.query}"
                if is_long_running_project_request(body.query):

                    def chat_job_progress(
                        progress: AutopilotProgress,
                        audit: AuditProgress | None,
                        event: str,
                    ) -> None:
                        payload: dict[str, object] = progress.as_dict()
                        if audit is not None:
                            payload["audit"] = audit.as_dict()
                        event_name = {
                            "replanned": "job_replanned",
                            "verification": "job_verification",
                        }.get(event, "job_progress")
                        emit(event_name, payload)
                        if cancelled.is_set() and not progress.terminal:
                            with AutopilotStore(config.autopilot_database) as store:
                                store.set_control_status(
                                    progress.job_id,
                                    "cancelled",
                                )

                    answer = runtime.run_autopilot_job(
                        query,
                        thread_id=body.thread_id,
                        allow_write=body.allow_write,
                        progress_callback=chat_job_progress,
                        diagnostic_source="web",
                        diagnostic_task_id=task_id,
                    )
                else:
                    answer = runtime.ask(
                        query,
                        thread_id=body.thread_id,
                        auto_context=body.auto_context,
                        diagnostic_source="web",
                        diagnostic_task_id=task_id,
                        diagnostic_request_id=task_id,
                    )
                metadata = runtime._provider_failover_middleware.runtime_metadata()
                emit("message", {"text": answer, "runtime": metadata})
                return {"answer": answer, "runtime": metadata}

        tasks.submit(
            "chat",
            operation,
            task_id=task_id,
            request_id=task_id,
        )
        return _request_payload(request, task_id=task_id)

    @app.post("/api/chat/{task_id}/cancel")
    def cancel_chat(request: Request, task_id: str):
        try:
            tasks.cancel(task_id)
        except KeyError as exc:
            raise HTTPException(404, "Task not found") from exc
        return _request_payload(request, task_id=task_id, status="cancelling")

    @app.get("/api/events/{task_id}")
    def task_events(request: Request, task_id: str):
        try:
            task = tasks.get(task_id)
        except KeyError as exc:
            raise HTTPException(404, "Task not found") from exc

        def stream() -> Iterator[str]:
            if (
                task.done.is_set()
                and task.terminal_event is not None
                and task.events.empty()
            ):
                terminal = task.terminal_event
                event_name = str(terminal["event"])
                data = json.dumps(terminal["data"], ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data}\n\n"
                return
            while True:
                try:
                    event = task.events.get(timeout=10)
                except queue.Empty:
                    if task.done.is_set():
                        if task.terminal_event is not None:
                            event = task.terminal_event
                            event_name = str(event["event"])
                            data = json.dumps(event["data"], ensure_ascii=False)
                            yield f"event: {event_name}\ndata: {data}\n\n"
                        break
                    yield ": heartbeat\n\n"
                    continue
                event_name = str(event["event"])
                data = json.dumps(event["data"], ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data}\n\n"
                if event_name in {"completed", "cancelled", "failed"}:
                    break

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/tasks/{task_id}")
    def task_status(request: Request, task_id: str):
        try:
            task = tasks.get(task_id)
        except KeyError as exc:
            raise HTTPException(404, "Task not found") from exc
        terminal = task.terminal_event
        return _request_payload(
            request,
            task_id=task.task_id,
            kind=task.kind,
            status=(
                str(terminal["event"])
                if terminal is not None
                else ("running" if not task.done.is_set() else "finished")
            ),
            terminal=(terminal["data"] if terminal is not None else None),
        )

    @app.get("/api/diagnostics")
    def diagnostic_requests(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        status: str | None = Query(default=None, max_length=50),
    ):
        items = diagnostics.list_requests(
            limit=limit,
            offset=offset,
            status=status,
        )
        return _request_payload(
            request,
            items=items,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/diagnostics/{request_id}")
    def diagnostic_details(
        request: Request,
        request_id: str,
        include_query: bool = False,
    ):
        if include_query and allow_remote:
            raise HTTPException(403, "Query disclosure is disabled in remote mode")
        try:
            item = diagnostics.request(request_id, include_query=include_query)
        except KeyError as exc:
            raise HTTPException(404, "Diagnostic request not found") from exc
        return _request_payload(request, item=item)

    @app.get("/api/diagnostics/{request_id}/export")
    def export_diagnostic(
        request: Request,
        request_id: str,
        include_query: bool = False,
    ):
        if include_query and allow_remote:
            raise HTTPException(403, "Query disclosure is disabled in remote mode")
        try:
            item = diagnostics.request(request_id, include_query=include_query)
        except KeyError as exc:
            raise HTTPException(404, "Diagnostic request not found") from exc
        return _request_payload(request, format="diagnostic-v1", item=item)

    @app.delete("/api/diagnostics")
    def purge_diagnostics(request: Request, body: DiagnosticPurgeRequest):
        if body.request_id is None and body.older_than_days is None:
            raise HTTPException(422, "Select request_id or older_than_days")
        deleted = diagnostics.purge(
            request_id=body.request_id,
            older_than_days=body.older_than_days,
        )
        return _request_payload(request, deleted=deleted)

    @app.post("/api/context/index", status_code=202)
    def index_context(request: Request, body: IndexRequest):
        def operation(
            _emit: Callable[[str, Mapping[str, object]], None],
            cancelled: threading.Event,
        ) -> object:
            if cancelled.is_set():
                raise _TaskCancelledError
            with ContextStore(
                config.context_database,
                chunk_size=config.chunk_size,
                chunk_overlap=config.chunk_overlap,
                max_file_bytes=config.max_file_bytes,
            ) as store:
                try:
                    report = store.index_path(body.path, config.context_root)
                except (ContextStoreError, PathSecurityError) as exc:
                    raise _PublicTaskError(
                        "context_index_failed",
                        "Не удалось индексировать выбранный путь внутри /workspace.",
                    ) from exc
            return {
                "files_indexed": report.files_indexed,
                "files_unchanged": report.files_unchanged,
                "files_skipped": report.files_skipped,
                "chunks_written": report.chunks_written,
                "error_count": len(report.errors),
                "errors": (
                    ["Некоторые файлы не удалось индексировать."]
                    if report.errors
                    else []
                ),
            }

        task_id = tasks.submit("context_index", operation)
        return _request_payload(request, task_id=task_id)

    @app.get("/api/context/sources")
    def context_sources(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        kind: str | None = None,
    ):
        with ContextStore(config.context_database) as store:
            sources = store.list_sources(limit=limit, offset=offset, kind=kind)
        return _request_payload(
            request,
            items=[
                {
                    "source": item.source,
                    "kind": item.kind,
                    "byte_size": item.byte_size,
                    "chunk_count": item.chunk_count,
                    "indexed_at": item.indexed_at,
                }
                for item in sources
            ],
            limit=limit,
            offset=offset,
        )

    @app.get("/api/context/search")
    def context_search(
        request: Request,
        query: str = Query(min_length=1, max_length=2_000),
        limit: int = Query(8, ge=1, le=100),
        source: str | None = None,
    ):
        with ContextStore(config.context_database) as store:
            hits = store.search(query, limit=limit, source=source)
        return _request_payload(
            request,
            items=[
                {
                    "source": hit.source,
                    "kind": hit.kind,
                    "content": hit.content,
                    "chunk_index": hit.chunk_index,
                    "score": hit.score,
                }
                for hit in hits
            ],
            result_count=len(hits),
        )

    @app.get("/api/context/window")
    def context_window(
        request: Request,
        source: str,
        chunk_index: int = Query(ge=0),
        radius: int = Query(2, ge=0, le=20),
    ):
        with ContextStore(config.context_database) as store:
            hits = store.context_window(source, chunk_index, radius=radius)
        return _request_payload(
            request,
            items=[
                {"chunk_index": hit.chunk_index, "content": hit.content} for hit in hits
            ],
        )

    def submit_job(body: JobRequest) -> tuple[str, str]:
        task_id = uuid4().hex
        include = tuple(body.include_patterns)
        exclude = tuple(body.exclude_patterns)
        job_id = AutopilotStore.job_id_for(
            thread_id=body.thread_id,
            objective=body.objective,
            workspace=config.workspace,
            allow_write=body.allow_write,
            include_patterns=include,
            exclude_patterns=exclude,
        )

        def operation(
            emit: Callable[[str, Mapping[str, object]], None],
            cancelled: threading.Event,
        ) -> object:
            def progress_callback(
                progress: AutopilotProgress,
                audit: AuditProgress | None,
                event: str,
            ) -> None:
                payload: dict[str, object] = progress.as_dict()
                if audit is not None:
                    payload["audit"] = audit.as_dict()
                event_name = {
                    "replanned": "job_replanned",
                    "verification": "job_verification",
                }.get(event, "job_progress")
                emit(event_name, payload)
                if cancelled.is_set() and not progress.terminal:
                    with AutopilotStore(config.autopilot_database) as store:
                        store.set_control_status(progress.job_id, "cancelled")

            current_providers = provider_registry.snapshot()
            with _runtime_factory(config, current_providers) as runtime:
                return runtime.run_autopilot_job(
                    body.objective,
                    thread_id=body.thread_id,
                    allow_write=body.allow_write,
                    include_patterns=include,
                    exclude_patterns=exclude,
                    progress_callback=progress_callback,
                    diagnostic_source="web",
                    diagnostic_task_id=task_id,
                )

        tasks.submit(
            "autopilot",
            operation,
            task_id=task_id,
            request_id=task_id,
        )
        return job_id, task_id

    @app.get("/api/jobs")
    def jobs(request: Request, limit: int = Query(50, ge=1, le=100)):
        with AutopilotStore(config.autopilot_database) as store:
            items = store.list_jobs(workspace=config.workspace, limit=limit)
        return _request_payload(request, items=items)

    @app.post("/api/jobs", status_code=202)
    def create_job(request: Request, body: JobRequest):
        job_id, task_id = submit_job(body)
        return _request_payload(
            request,
            job_id=job_id,
            task_id=task_id,
            mode="allow-write" if body.allow_write else "read-only",
        )

    @app.get("/api/jobs/{job_id}")
    def job_details(request: Request, job_id: str):
        with AutopilotStore(config.autopilot_database) as store:
            try:
                details = store.details(job_id)
            except ValueError as exc:
                raise HTTPException(404, "Autopilot job not found") from exc
        physical_workspace = str(config.workspace)
        details["workspace"] = "/workspace"
        for key in ("objective", "last_error_message", "report"):
            value = details.get(key)
            if isinstance(value, str):
                details[key] = value.replace(physical_workspace, "/workspace")
        for collection_key in ("verification_results", "work_units"):
            collection = details.get(collection_key)
            if isinstance(collection, list):
                for item in collection:
                    if not isinstance(item, dict):
                        continue
                    for key in ("output", "summary"):
                        value = item.get(key)
                        if isinstance(value, str):
                            item[key] = value.replace(
                                physical_workspace,
                                "/workspace",
                            )
        return _request_payload(request, job=details)

    @app.post("/api/jobs/{job_id}/pause")
    def pause_job(request: Request, job_id: str):
        with AutopilotStore(config.autopilot_database) as store:
            try:
                progress = store.set_control_status(job_id, "paused")
            except ValueError as exc:
                raise HTTPException(404, "Autopilot job not found") from exc
        return _request_payload(request, progress=progress.as_dict())

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(request: Request, job_id: str):
        with AutopilotStore(config.autopilot_database) as store:
            try:
                progress = store.set_control_status(job_id, "cancelled")
            except ValueError as exc:
                raise HTTPException(404, "Autopilot job not found") from exc
        return _request_payload(request, progress=progress.as_dict())

    @app.post("/api/jobs/{job_id}/resume", status_code=202)
    def resume_job(request: Request, job_id: str):
        with AutopilotStore(config.autopilot_database) as store:
            try:
                details = store.details(job_id)
            except ValueError as exc:
                raise HTTPException(404, "Autopilot job not found") from exc
        raw_include = details.get("include_patterns")
        raw_exclude = details.get("exclude_patterns")
        body = JobRequest(
            objective=str(details["objective"]),
            thread_id=str(details["thread_id"]),
            allow_write=details["mode"] == "allow-write",
            include_patterns=(
                [str(item) for item in raw_include]
                if isinstance(raw_include, list)
                else []
            ),
            exclude_patterns=(
                [str(item) for item in raw_exclude]
                if isinstance(raw_exclude, list)
                else []
            ),
        )
        resumed_job_id, task_id = submit_job(body)
        return _request_payload(request, job_id=resumed_job_id, task_id=task_id)

    @app.get("/api/jobs/{job_id}/report")
    def job_report(request: Request, job_id: str):
        with AutopilotStore(config.autopilot_database) as store:
            try:
                details = store.details(job_id, unit_limit=1)
            except ValueError as exc:
                raise HTTPException(404, "Autopilot job not found") from exc
        return PlainTextResponse(
            str(details.get("report") or "").replace(
                str(config.workspace),
                "/workspace",
            ),
            headers={"X-Request-ID": request.state.request_id},
        )

    def submit_audit(body: AuditRequest) -> str:
        task_id = uuid4().hex

        def operation(
            emit: Callable[[str, Mapping[str, object]], None],
            cancelled: threading.Event,
        ) -> object:
            def progress_callback(
                progress: AuditProgress,
                batch_number: int,
                processed_count: int,
            ) -> None:
                emit(
                    "audit_progress",
                    {
                        **progress.as_dict(),
                        "batch_number": batch_number,
                        "processed_count": processed_count,
                    },
                )
                if cancelled.is_set():
                    raise _TaskCancelledError

            current_providers = provider_registry.snapshot()
            with _runtime_factory(config, current_providers) as runtime:
                return runtime.run_project_audit(
                    body.objective,
                    thread_id=body.thread_id,
                    max_batches=body.max_batches,
                    allow_write=body.allow_write,
                    include_patterns=body.include_patterns,
                    exclude_patterns=body.exclude_patterns,
                    batch_size=body.batch_size,
                    progress_callback=progress_callback,
                    diagnostic_source="web",
                    diagnostic_task_id=task_id,
                )

        tasks.submit("audit", operation, task_id=task_id)
        return task_id

    @app.get("/api/audits")
    def audits(request: Request, limit: int = Query(50, ge=1, le=100)):
        with ProjectAuditStore(config.project_audit_database) as store:
            items = store.list_runs(workspace=config.workspace, limit=limit)
        return _request_payload(request, items=items)

    @app.post("/api/audits", status_code=202)
    def create_audit(request: Request, body: AuditRequest):
        task_id = submit_audit(body)
        return _request_payload(
            request,
            task_id=task_id,
            mode=("allow-write" if body.allow_write else "read-only"),
        )

    @app.get("/api/audits/{run_id}")
    def audit_details(request: Request, run_id: str):
        with ProjectAuditStore(config.project_audit_database) as store:
            try:
                details = store.run_details(run_id)
            except ValueError as exc:
                raise HTTPException(404, "Audit run not found") from exc
        return _request_payload(request, audit=details)

    @app.post("/api/audits/{run_id}/pause")
    def pause_audit(request: Request, run_id: str):
        with ProjectAuditStore(config.project_audit_database) as store:
            try:
                progress = store.set_run_status(run_id, "paused")
            except ValueError as exc:
                raise HTTPException(404, "Audit run not found") from exc
        return _request_payload(request, progress=progress.as_dict())

    @app.post("/api/audits/{run_id}/cancel")
    def cancel_audit(request: Request, run_id: str):
        with ProjectAuditStore(config.project_audit_database) as store:
            try:
                progress = store.set_run_status(run_id, "cancelled")
            except ValueError as exc:
                raise HTTPException(404, "Audit run not found") from exc
        return _request_payload(request, progress=progress.as_dict())

    @app.post("/api/audits/{run_id}/resume", status_code=202)
    def resume_audit(request: Request, run_id: str):
        with ProjectAuditStore(config.project_audit_database) as store:
            try:
                details = store.run_details(run_id)
            except ValueError as exc:
                raise HTTPException(404, "Audit run not found") from exc
        raw_include = details.get("include_patterns")
        raw_exclude = details.get("exclude_patterns")
        include = raw_include if isinstance(raw_include, list) else []
        exclude = raw_exclude if isinstance(raw_exclude, list) else []
        body = AuditRequest(
            objective=str(details["objective"]),
            thread_id=str(details["thread_id"]),
            allow_write=details["mode"] == "allow-write",
            batch_size=int(str(details["batch_size"])),
            include_patterns=[str(item) for item in include],
            exclude_patterns=[str(item) for item in exclude],
        )
        return _request_payload(request, task_id=submit_audit(body))

    @app.get("/api/audits/{run_id}/findings")
    def audit_findings(
        request: Request,
        run_id: str,
        limit: int = Query(100, ge=1, le=2_000),
    ):
        with ProjectAuditStore(config.project_audit_database) as store:
            items = store.list_findings(run_id, limit=limit)
        return _request_payload(request, items=items)

    @app.get("/api/audits/{run_id}/requirements")
    def audit_requirements(request: Request, run_id: str):
        with ProjectAuditStore(config.project_audit_database) as store:
            items = store.list_requirements(run_id)
        return _request_payload(request, items=items)

    @app.get("/api/audits/{run_id}/report")
    def audit_report(
        request: Request,
        run_id: str,
        report_format: str = Query("text", alias="format"),
    ):
        if report_format not in {"text", "json"}:
            raise HTTPException(422, "Report format must be text or json")
        with ProjectAuditStore(config.project_audit_database) as store:
            try:
                report = store.render_report(run_id, report_format)
            except ValueError as exc:
                raise HTTPException(404, "Audit run not found") from exc
        headers = {"X-Request-ID": request.state.request_id}
        if report_format == "json":
            return FastAPIResponse(
                report,
                media_type="application/json",
                headers=headers,
            )
        return PlainTextResponse(report, headers=headers)

    def safe_file(virtual_path: str, *, must_exist: bool) -> Path:
        normalized = virtual_path.replace("\\", "/").strip()
        if normalized in {"", "/", "/workspace", "/workspace/"}:
            requested = "/workspace"
        elif normalized.startswith("/workspace/"):
            requested = normalized
        else:
            requested = "/workspace/" + normalized.lstrip("/")
        try:
            path = resolve_inside(
                config.workspace,
                requested,
                must_exist=must_exist,
            )
        except (OSError, PathSecurityError) as exc:
            raise HTTPException(404, "Workspace path not found") from exc
        if _is_secret_path(path.relative_to(config.workspace)):
            raise HTTPException(404, "Workspace path not found")
        return path

    @app.get("/api/files")
    def list_files(
        request: Request,
        path: str = "",
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        directory = safe_file(path, must_exist=True)
        if not directory.is_dir():
            raise HTTPException(400, "Path is not a directory")
        entries = [
            entry
            for entry in directory.iterdir()
            if not _is_secret_path(entry.relative_to(config.workspace))
            and not entry.is_symlink()
        ]
        entries.sort(key=lambda item: (not item.is_dir(), item.name.casefold()))
        page = entries[offset : offset + limit]
        return _request_payload(
            request,
            items=[
                {
                    "name": entry.name,
                    "path": "/workspace/"
                    + entry.relative_to(config.workspace).as_posix(),
                    "type": "directory" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else None,
                }
                for entry in page
            ],
            limit=limit,
            offset=offset,
        )

    @app.get("/api/files/{virtual_path:path}")
    def read_file(
        request: Request,
        virtual_path: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1_000),
    ):
        path = safe_file(virtual_path, must_exist=True)
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise HTTPException(413, "File is not a bounded text file")
        raw = path.read_bytes()
        if b"\0" in raw[:4096]:
            raise HTTPException(415, "Binary preview is not supported")
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        lines = text.splitlines(keepends=True)
        return _request_payload(
            request,
            path="/workspace/" + path.relative_to(config.workspace).as_posix(),
            content="".join(lines[offset : offset + limit]),
            offset=offset,
            next_offset=(offset + limit if offset + limit < len(lines) else None),
            sha256=hashlib.sha256(raw).hexdigest(),
            total_lines=len(lines),
        )

    @app.put("/api/files/{virtual_path:path}")
    def update_file(request: Request, virtual_path: str, body: FileWriteRequest):
        path = safe_file(virtual_path, must_exist=True)
        if not path.is_file():
            raise HTTPException(400, "Path is not a file")
        current_hash = _file_sha256(path)
        if body.expected_sha256 is None or body.expected_sha256 != current_hash:
            raise HTTPException(409, "File changed; reload before saving")
        path.write_text(body.content, encoding="utf-8", newline="\n")
        return _request_payload(request, path=virtual_path, sha256=_file_sha256(path))

    @app.post("/api/files/{virtual_path:path}", status_code=201)
    def create_file(request: Request, virtual_path: str, body: FileWriteRequest):
        path = safe_file(virtual_path, must_exist=False)
        if path.exists():
            raise HTTPException(409, "Path already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.content, encoding="utf-8", newline="\n")
        return _request_payload(request, path=virtual_path, sha256=_file_sha256(path))

    @app.delete("/api/files/{virtual_path:path}")
    def delete_file(request: Request, virtual_path: str, body: FileDeleteRequest):
        if os.getenv("AGENT_WEB_ALLOW_DELETE", "").casefold() not in {"1", "true"}:
            raise HTTPException(403, "Web file deletion is disabled")
        expected = "/workspace/" + virtual_path.lstrip("/")
        if body.confirm_path.replace("\\", "/") != expected:
            raise HTTPException(409, "Exact virtual path confirmation is required")
        path = safe_file(virtual_path, must_exist=True)
        if path == config.workspace:
            raise HTTPException(403, "Workspace root cannot be deleted")
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        return _request_payload(request, path=expected, deleted=True)

    @app.get("/api/providers")
    def provider_list(request: Request):
        return _request_payload(
            request,
            items=provider_registry.catalog(),
            active=[item.name for item in provider_registry.snapshot()],
        )

    @app.post("/api/providers", status_code=201)
    def create_provider(request: Request, body: ProviderCreateRequest):
        try:
            provider = _custom_provider(body)
            provider_registry.add_custom(provider)
        except ConfigurationError as exc:
            status_code = 409 if "already exists" in str(exc) else 422
            raise HTTPException(status_code, str(exc)) from exc
        return _request_payload(
            request,
            provider={
                "provider": provider.name,
                "model": provider.model,
                "base_url": provider.base_url,
                "configured": True,
                "local": _is_loopback_base_url(provider.base_url),
                "api_key_environment": _custom_api_key_environment(provider.name),
            },
            effective_immediately=True,
        )

    @app.put("/api/providers/priority")
    def update_provider_priority(request: Request, body: ProviderPriorityRequest):
        try:
            updated = provider_registry.replace(body.providers)
        except ConfigurationError as exc:
            raise HTTPException(
                422,
                "Провайдер не настроен. Добавьте API-ключ и параметры на сервере.",
            ) from exc
        return _request_payload(
            request,
            active=[item.name for item in updated],
            effective_immediately=True,
        )

    @app.post("/api/providers/doctor")
    def provider_doctor(request: Request, body: ProviderDoctorRequest):
        if not body.live:
            return _request_payload(request, status="configured", live=False)

        def operation(
            _emit: Callable[[str, Mapping[str, object]], None],
            cancelled: threading.Event,
        ) -> object:
            failures: list[str] = []
            for provider in provider_registry.snapshot():
                if cancelled.is_set():
                    raise _TaskCancelledError
                try:
                    response = create_chat_model(provider).invoke(
                        "Reply with exactly: OK"
                    )
                except Exception as exc:
                    failures.append(f"{provider.name}:{type(exc).__name__}")
                    continue
                return {
                    "provider": provider.name,
                    "response": message_text(response).strip(),
                }
            raise AgentError("All providers failed: " + ", ".join(failures))

        return _request_payload(request, task_id=tasks.submit("doctor", operation))

    @app.post("/api/providers/{provider_name}/doctor")
    def single_provider_doctor(
        request: Request,
        provider_name: str,
        body: ProviderDoctorRequest,
    ):
        catalog = provider_registry.catalog()
        known = next(
            (item for item in catalog if item["provider"] == provider_name.casefold()),
            None,
        )
        if known is None:
            raise HTTPException(404, "Provider not found")
        if not bool(known["configured"]):
            raise HTTPException(422, "Провайдер ещё не настроен на сервере")
        if not body.live:
            return _request_payload(
                request,
                provider=provider_name.casefold(),
                status="configured",
                live=False,
            )

        def operation(
            _emit: Callable[[str, Mapping[str, object]], None],
            cancelled: threading.Event,
        ) -> object:
            if cancelled.is_set():
                raise _TaskCancelledError
            candidate = provider_registry.get(provider_name)
            if candidate is None:
                try:
                    candidate = ProviderConfig.from_env(provider_name)
                except ConfigurationError:
                    raise _PublicTaskError(
                        "provider_not_configured",
                        "Провайдер не настроен на сервере.",
                    ) from None
            available_models: tuple[str, ...] = ()
            local = _is_loopback_base_url(candidate.base_url)
            if local:
                available_models = _probe_openai_models(candidate)
                if candidate.model not in available_models:
                    is_default_lmstudio = (
                        candidate.name == "lmstudio"
                        and candidate.model == "local-model"
                    )
                    if is_default_lmstudio:
                        chat_models = tuple(
                            model
                            for model in available_models
                            if not any(
                                marker in model.casefold()
                                for marker in ("embed", "rerank")
                            )
                        )
                        selected_model = (chat_models or available_models)[0]
                        candidate = replace(candidate, model=selected_model)
                        provider_registry.update(candidate)
                    else:
                        raise _PublicTaskError(
                            "provider_model_not_loaded",
                            "Сервер доступен, но выбранная модель не загружена.",
                        )
            try:
                response = create_chat_model(candidate).invoke("Reply with exactly: OK")
            except Exception as exc:
                message = (
                    "LM Studio доступна, но локальная модель не ответила. "
                    "Проверьте поддержку Chat Completions и tool calling."
                    if candidate.name == "lmstudio"
                    else "Провайдер не ответил на live-проверку."
                )
                raise _PublicTaskError(
                    "provider_unavailable",
                    message,
                ) from exc
            return {
                "provider": candidate.name,
                "model": candidate.model,
                "response": message_text(response).strip(),
                "local": local,
                "available_models": list(available_models),
            }

        return _request_payload(
            request,
            task_id=tasks.submit("provider_doctor", operation),
        )

    @app.get("/api/settings")
    def settings(request: Request):
        current = AppConfig.from_env(config.project_root)
        values = {key: getattr(current, key) for key in _SETTINGS}
        items = [
            {"name": key, "value": values[key], **definition}
            for key, definition in _SETTINGS.items()
        ]
        return _request_payload(request, values=values, items=items)

    @app.put("/api/settings")
    def update_settings(request: Request, body: SettingsRequest):
        nonlocal config
        unknown = set(body.values) - set(_SETTINGS)
        if unknown:
            raise HTTPException(422, "Unsupported setting")
        for name, value in body.values.items():
            definition = _SETTINGS[name]
            minimum = float(str(definition["minimum"]))
            maximum = float(str(definition["maximum"]))
            if isinstance(value, bool) or not minimum <= float(value) <= maximum:
                raise HTTPException(422, "Setting value is outside allowed bounds")
        previous: dict[str, str | None] = {
            name: os.environ.get(str(definition["environment"]))
            for name, definition in _SETTINGS.items()
        }
        try:
            for name, value in body.values.items():
                os.environ[str(_SETTINGS[name]["environment"])] = str(value)
            updated_config = AppConfig.from_env(config.project_root)
            updated_config.prepare_directories()
        except Exception:
            for name, previous_value in previous.items():
                env = str(_SETTINGS[name]["environment"])
                if previous_value is None:
                    os.environ.pop(env, None)
                else:
                    os.environ[env] = previous_value
            raise HTTPException(422, "Setting value is invalid") from None
        config = updated_config
        app.state.config = config
        return _request_payload(request, updated=sorted(body.values))

    @app.get("/assets/{asset_name}")
    def asset(asset_name: str) -> FileResponse:
        if asset_name not in {"app.js", "styles.css"}:
            raise HTTPException(404, "Asset not found")
        return FileResponse(_STATIC_ROOT / asset_name)

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FastAPIResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "API endpoint not found")
        return FileResponse(_STATIC_ROOT / "index.html")

    return app
