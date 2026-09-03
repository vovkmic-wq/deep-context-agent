"""Durable, sanitized diagnostics for requests and background Web tasks."""

# ruff: noqa: RUF001 -- Russian operator messages are intentional.

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from context_agent.errors import DiagnosticStoreError

FailureLogMode = Literal["off", "metadata", "redacted", "full"]

_ASSIGNMENT_SECRET_PATTERN = re.compile(
    r"(?i)\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)(\bauthorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;\"']+"
)
_COOKIE_PATTERN = re.compile(r"(?im)^(set-cookie|cookie)\s*:\s*[^\r\n]+$")
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|password)=)[^&#\s]+"
)
_PROVIDER_KEY_PATTERN = re.compile(
    r"(?i)\b(?:sk-(?:proj-)?|zai[-_]|ya29\.)[a-z0-9_-]{10,}"
)
_MARKED_SECRET_PATTERN = re.compile(r"(?iu)\b(?:DO_NOT_SHOW|НЕ_ПОКАЗЫВАТЬ)[=:][^\s,;]+")
_TERMINAL_EVENTS = frozenset({"completed", "cancelled", "failed"})
_SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def utc_now() -> str:
    """Return a stable UTC timestamp for persisted diagnostic records."""

    return datetime.now(UTC).isoformat()


def redact_sensitive_text(
    text: str,
    *,
    known_secrets: Sequence[str] = (),
) -> str:
    """Remove common credentials and configured secret values from text."""

    redacted = _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", text)
    redacted = _ASSIGNMENT_SECRET_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = _COOKIE_PATTERN.sub(r"\1: [REDACTED]", redacted)
    redacted = _QUERY_SECRET_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = _PROVIDER_KEY_PATTERN.sub("[REDACTED]", redacted)
    redacted = _MARKED_SECRET_PATTERN.sub("[REDACTED]", redacted)
    for secret in sorted(
        {item for item in known_secrets if len(item) >= 6},
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def classify_failure(exc: BaseException) -> str:
    """Classify an exception chain without exposing its raw payload."""

    parts: list[str] = []
    current: BaseException | None = exc
    for _depth in range(8):
        if current is None:
            break
        parts.extend((type(current).__name__, str(current)))
        current = current.__cause__ or current.__context__
    normalized = " ".join(parts).casefold()
    patterns = (
        (
            "context_window_exceeded",
            (
                "context_length",
                "context window",
                "maximum context length",
                "too many tokens",
                "prompt is too long",
            ),
        ),
        (
            "quota_exhausted",
            ("insufficient_quota", "credit_balance_exhausted", "no credits"),
        ),
        ("rate_limited", ("ratelimit", "rate limit", "too many requests", "429")),
        (
            "authentication_failed",
            ("authentication", "unauthorized", "invalid api key", "401"),
        ),
        ("provider_timeout", ("timeout", "timed out")),
        (
            "provider_unavailable",
            (
                "connectionerror",
                "connection error",
                "connection reset",
                "service unavailable",
                "bad gateway",
            ),
        ),
        ("agent_step_limit", ("graphrecursionerror", "recursion limit")),
        (
            "autopilot_lease_lost",
            ("autopilot lease ownership", "autopilotleaseerror"),
        ),
    )
    return next(
        (
            code
            for code, markers in patterns
            if any(marker in normalized for marker in markers)
        ),
        "provider_chain_failed",
    )


def safe_exception_chain(
    exc: BaseException,
    *,
    known_secrets: Sequence[str] = (),
) -> list[dict[str, str]]:
    """Return bounded exception types and redacted messages."""

    chain: list[dict[str, str]] = []
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending and len(chain) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(
            {
                "type": type(current).__name__[:200],
                "message": redact_sensitive_text(
                    str(current), known_secrets=known_secrets
                )[:2_000],
            }
        )
        if isinstance(current, BaseExceptionGroup):
            pending[0:0] = list(current.exceptions)
        else:
            linked = current.__cause__ or current.__context__
            if linked is not None:
                pending.append(linked)
    return chain


def _bounded_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    payload = text.encode("utf-8")
    if len(payload) <= max_bytes:
        return text, False
    marker = b"\n...[TRUNCATED]...\n"
    budget = max(0, max_bytes - len(marker))
    head_size = budget // 2
    tail_size = budget - head_size
    head = payload[:head_size].decode("utf-8", errors="ignore")
    tail = payload[-tail_size:].decode("utf-8", errors="ignore") if tail_size else ""
    return f"{head}{marker.decode()}{tail}", True


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _safe_name(value: object, *, fallback: str = "unknown") -> str:
    candidate = str(value)
    return candidate if _SAFE_NAME_PATTERN.fullmatch(candidate) else fallback


def _safe_nonnegative_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def _neutral_query_preview(text: str) -> str:
    """Describe prompt shape without retaining any user-supplied atom."""

    return _json(
        {
            "characters": len(text),
            "lines": text.count("\n") + 1,
            "words": len(text.split()),
        }
    )


class DiagnosticStore:
    """Thread-safe SQLite journal deliberately isolated from agent rollback."""

    schema_version = 2

    def __init__(
        self,
        database_path: Path,
        *,
        mode: FailureLogMode = "redacted",
        retention_days: int = 30,
        max_rows: int = 10_000,
        query_max_bytes: int = 65_536,
        known_secrets: Sequence[str] = (),
    ) -> None:
        self.database_path = database_path.resolve()
        self.mode = mode
        self.retention_days = retention_days
        self.max_rows = max_rows
        self.query_max_bytes = query_max_bytes
        self.known_secrets = tuple(known_secrets)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
            timeout=10,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._closed = False
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        schema = """
        PRAGMA foreign_keys = ON;
        PRAGMA journal_mode = WAL;
        PRAGMA busy_timeout = 10000;

        CREATE TABLE IF NOT EXISTS request_attempts (
            request_id TEXT PRIMARY KEY,
            parent_request_id TEXT,
            task_id TEXT,
            thread_id TEXT NOT NULL,
            operation_kind TEXT NOT NULL,
            source TEXT NOT NULL,
            app_version TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            finished_at_utc TEXT,
            duration_ms INTEGER,
            query_mode TEXT NOT NULL,
            query_sha256 TEXT NOT NULL,
            query_text TEXT,
            query_preview TEXT,
            query_bytes INTEGER NOT NULL,
            query_truncated INTEGER NOT NULL DEFAULT 0,
            provider_priority_json TEXT NOT NULL,
            provider_attempts_json TEXT NOT NULL DEFAULT '[]',
            tool_audit_json TEXT NOT NULL DEFAULT '[]',
            baseline_checkpoint_id TEXT,
            rollback_attempted INTEGER NOT NULL DEFAULT 0,
            rollback_success INTEGER,
            rollback_checkpoint_rows INTEGER NOT NULL DEFAULT 0,
            rollback_write_rows INTEGER NOT NULL DEFAULT 0,
            filesystem_side_effects INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            exception_chain_json TEXT NOT NULL DEFAULT '[]',
            retryable INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS request_attempts_created_idx
        ON request_attempts(created_at_utc DESC);
        CREATE INDEX IF NOT EXISTS request_attempts_status_idx
        ON request_attempts(status, created_at_utc DESC);
        CREATE INDEX IF NOT EXISTS request_attempts_task_idx
        ON request_attempts(task_id);
        CREATE INDEX IF NOT EXISTS request_attempts_thread_idx
        ON request_attempts(thread_id, created_at_utc DESC);

        CREATE TABLE IF NOT EXISTS provider_attempt_records (
            request_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            error_type TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            outcome TEXT NOT NULL,
            PRIMARY KEY(request_id, ordinal),
            FOREIGN KEY(request_id) REFERENCES request_attempts(request_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS web_tasks (
            task_id TEXT PRIMARY KEY,
            request_id TEXT,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            terminal_event_json TEXT,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );
        """
        try:
            with self._lock, self._connection:
                self._connection.executescript(schema)
                current = int(
                    self._connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if current > self.schema_version:
                    raise DiagnosticStoreError(
                        "Diagnostics schema is newer than this application"
                    )
                columns = {
                    str(row[1])
                    for row in self._connection.execute(
                        "PRAGMA table_info(request_attempts)"
                    ).fetchall()
                }
                if "query_preview" not in columns:
                    self._connection.execute(
                        "ALTER TABLE request_attempts ADD COLUMN query_preview TEXT"
                    )
                self._connection.execute(f"PRAGMA user_version={self.schema_version}")
        except sqlite3.Error as exc:
            raise DiagnosticStoreError("Cannot initialize diagnostics SQLite") from exc

    def _query_payload(self, query: str) -> tuple[str | None, str, str, int, bool]:
        raw = query.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        if self.mode in {"off", "metadata"}:
            return None, _neutral_query_preview(query), digest, len(raw), False
        stored = (
            query
            if self.mode == "full"
            else redact_sensitive_text(query, known_secrets=self.known_secrets)
        )
        bounded, truncated = _bounded_utf8(stored, self.query_max_bytes)
        return bounded, _neutral_query_preview(query), digest, len(raw), truncated

    def start_request(
        self,
        *,
        query: str,
        thread_id: str,
        operation_kind: str,
        source: str,
        app_version: str,
        provider_priority: Sequence[Mapping[str, object]],
        baseline_checkpoint_id: str | None,
        task_id: str | None = None,
        request_id: str | None = None,
        parent_request_id: str | None = None,
    ) -> str | None:
        """Persist an in-progress attempt before the model is invoked."""

        if self.mode == "off":
            return None
        selected_id = request_id or uuid4().hex
        (
            query_text,
            query_preview,
            query_hash,
            query_bytes,
            truncated,
        ) = self._query_payload(query)
        safe_priority = [
            {
                "provider": _safe_name(item.get("provider")),
                "model": redact_sensitive_text(
                    str(item.get("model", "unknown")),
                    known_secrets=self.known_secrets,
                )[:200],
                "base_url": redact_sensitive_text(
                    str(item.get("base_url", "")),
                    known_secrets=self.known_secrets,
                )[:500],
            }
            for item in provider_priority[:20]
        ]
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    INSERT INTO request_attempts(
                        request_id, parent_request_id, task_id, thread_id,
                        operation_kind, source, app_version, status,
                        created_at_utc, query_mode, query_sha256, query_text,
                        query_preview, query_bytes, query_truncated,
                        provider_priority_json,
                        baseline_checkpoint_id
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, 'in_progress',
                        ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        selected_id,
                        parent_request_id,
                        task_id,
                        thread_id[:200],
                        operation_kind[:100],
                        source[:50],
                        app_version[:50],
                        utc_now(),
                        self.mode,
                        query_hash,
                        query_text,
                        query_preview,
                        query_bytes,
                        int(truncated),
                        _json(safe_priority),
                        baseline_checkpoint_id,
                    ),
                )
            self.cleanup()
        except sqlite3.Error as exc:
            raise DiagnosticStoreError(
                "Cannot create the required diagnostic request record"
            ) from exc
        return selected_id

    def complete_request(
        self,
        request_id: str | None,
        *,
        provider_attempts: Sequence[Mapping[str, object]],
        tool_audit: Sequence[Mapping[str, object]],
        duration_ms: int,
    ) -> None:
        """Mark one request as successfully completed."""

        if request_id is None:
            return
        self._finish_request(
            request_id,
            status="completed",
            provider_attempts=provider_attempts,
            tool_audit=tool_audit,
            duration_ms=duration_ms,
        )

    def fail_request(
        self,
        request_id: str | None,
        *,
        exc: BaseException,
        provider_attempts: Sequence[Mapping[str, object]],
        tool_audit: Sequence[Mapping[str, object]],
        duration_ms: int,
        rollback_attempted: bool,
        rollback_success: bool,
        rollback_checkpoint_rows: int,
        rollback_write_rows: int,
        filesystem_side_effects: bool,
        error_code: str | None = None,
    ) -> None:
        """Persist a failed request after its rollback attempt."""

        if request_id is None:
            return
        self._finish_request(
            request_id,
            status="failed",
            provider_attempts=provider_attempts,
            tool_audit=tool_audit,
            duration_ms=duration_ms,
            rollback_attempted=rollback_attempted,
            rollback_success=rollback_success,
            rollback_checkpoint_rows=rollback_checkpoint_rows,
            rollback_write_rows=rollback_write_rows,
            filesystem_side_effects=filesystem_side_effects,
            error_code=error_code or classify_failure(exc),
            exception_chain=safe_exception_chain(exc, known_secrets=self.known_secrets),
            retryable=(error_code or classify_failure(exc))
            in {
                "context_window_exceeded",
                "rate_limited",
                "provider_timeout",
                "provider_unavailable",
            },
        )

    def _finish_request(
        self,
        request_id: str,
        *,
        status: str,
        provider_attempts: Sequence[Mapping[str, object]],
        tool_audit: Sequence[Mapping[str, object]],
        duration_ms: int,
        rollback_attempted: bool = False,
        rollback_success: bool | None = None,
        rollback_checkpoint_rows: int = 0,
        rollback_write_rows: int = 0,
        filesystem_side_effects: bool = False,
        error_code: str | None = None,
        exception_chain: Sequence[Mapping[str, object]] = (),
        retryable: bool = False,
    ) -> None:
        safe_provider_attempts = self._safe_provider_attempts(provider_attempts)
        safe_tool_audit = self._safe_tool_audit(tool_audit)
        try:
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    """
                    UPDATE request_attempts SET
                        status = ?, finished_at_utc = ?, duration_ms = ?,
                        provider_attempts_json = ?, tool_audit_json = ?,
                        rollback_attempted = ?, rollback_success = ?,
                        rollback_checkpoint_rows = ?, rollback_write_rows = ?,
                        filesystem_side_effects = ?, error_code = ?,
                        exception_chain_json = ?, retryable = ?
                    WHERE request_id = ?
                    """,
                    (
                        status,
                        utc_now(),
                        max(0, duration_ms),
                        _json(safe_provider_attempts),
                        _json(safe_tool_audit),
                        int(rollback_attempted),
                        None if rollback_success is None else int(rollback_success),
                        max(0, rollback_checkpoint_rows),
                        max(0, rollback_write_rows),
                        int(filesystem_side_effects),
                        error_code,
                        _json(exception_chain),
                        int(retryable),
                        request_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DiagnosticStoreError("Diagnostic request record is missing")
                self._connection.execute(
                    "DELETE FROM provider_attempt_records WHERE request_id = ?",
                    (request_id,),
                )
                self._connection.executemany(
                    """
                    INSERT INTO provider_attempt_records(
                        request_id, ordinal, provider, model, status,
                        error_type, retry_count, duration_ms, outcome
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            request_id,
                            attempt["ordinal"],
                            attempt["provider"],
                            attempt["model"],
                            attempt["status"],
                            attempt.get("error_type"),
                            attempt["retry_count"],
                            attempt["duration_ms"],
                            attempt["outcome"],
                        )
                        for attempt in safe_provider_attempts
                    ],
                )
        except sqlite3.Error as exc:
            raise DiagnosticStoreError("Cannot finalize diagnostic request") from exc
        self.cleanup()

    def _safe_provider_attempts(
        self,
        attempts: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        """Accept only bounded, non-secret provider-attempt fields."""

        sanitized: list[dict[str, object]] = []
        for fallback_ordinal, attempt in enumerate(attempts[:100], start=1):
            raw_ordinal = attempt.get("ordinal", fallback_ordinal)
            ordinal = raw_ordinal if isinstance(raw_ordinal, int) else fallback_ordinal
            sanitized.append(
                {
                    "ordinal": max(1, ordinal),
                    "provider": _safe_name(attempt.get("provider")),
                    "model": redact_sensitive_text(
                        str(attempt.get("model", "unknown")),
                        known_secrets=self.known_secrets,
                    )[:200],
                    "status": _safe_name(attempt.get("status")),
                    "error_type": (
                        _safe_name(attempt.get("error_type"))
                        if attempt.get("error_type")
                        else None
                    ),
                    "retry_count": _safe_nonnegative_int(attempt.get("retry_count", 0)),
                    "duration_ms": _safe_nonnegative_int(attempt.get("duration_ms", 0)),
                    "outcome": _safe_name(attempt.get("outcome", "unknown")),
                }
            )
        return sanitized

    def _safe_tool_audit(
        self,
        entries: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        """Drop raw arguments/results and retain bounded virtual evidence only."""

        sanitized: list[dict[str, object]] = []
        for entry in entries[:200]:
            raw_path = entry.get("path")
            path = None
            if isinstance(raw_path, str) and raw_path.startswith("/workspace"):
                path = redact_sensitive_text(
                    raw_path,
                    known_secrets=self.known_secrets,
                )[:2_000]
            raw_count = entry.get("result_count")
            result_count = (
                max(0, raw_count)
                if isinstance(raw_count, int) and not isinstance(raw_count, bool)
                else None
            )
            raw_hash = entry.get("content_sha256")
            content_hash = (
                raw_hash
                if isinstance(raw_hash, str) and _SHA256_PATTERN.fullmatch(raw_hash)
                else None
            )
            sanitized.append(
                {
                    "name": _safe_name(entry.get("name")),
                    "path": path,
                    "status": _safe_name(entry.get("status")),
                    "result_count": result_count,
                    "content_sha256": content_hash,
                }
            )
        return sanitized

    def record_task_start(
        self,
        task_id: str,
        kind: str,
        *,
        request_id: str | None = None,
    ) -> None:
        """Create a persistent Web task before its worker starts."""

        now = utc_now()
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    INSERT INTO web_tasks(
                        task_id, request_id, kind, status,
                        terminal_event_json, created_at_utc, updated_at_utc
                    ) VALUES (?, ?, ?, 'running', NULL, ?, ?)
                    """,
                    (task_id, request_id, kind[:100], now, now),
                )
        except sqlite3.Error as exc:
            raise DiagnosticStoreError("Cannot create persistent Web task") from exc

    def record_task_terminal(
        self,
        task_id: str,
        event: Mapping[str, object],
    ) -> None:
        """Store one sanitized terminal event for restart-safe replay."""

        event_name = str(event.get("event", ""))
        if event_name not in _TERMINAL_EVENTS:
            raise ValueError("Only terminal task events can be persisted")
        raw_data = event.get("data", {})
        data = raw_data if isinstance(raw_data, Mapping) else {}
        safe_data: dict[str, object] = {}
        for name in ("task_id", "request_id", "error_type", "provider", "model"):
            if data.get(name) is not None:
                safe_data[name] = _safe_name(data[name])
        for name in (
            "duration_ms",
            "failover_count",
            "files_indexed",
            "files_unchanged",
            "files_skipped",
            "files_scanned",
            "found_files",
            "matched",
            "excluded",
            "chunks_written",
            "error_count",
        ):
            value = data.get(name)
            if isinstance(value, int) and value >= 0:
                safe_data[name] = value
        if "retryable" in data:
            safe_data["retryable"] = bool(data["retryable"])
        for name in ("partial", "cursor_available"):
            if name in data:
                safe_data[name] = bool(data[name])
        if data.get("partial_reason") is not None:
            safe_data["partial_reason"] = _safe_name(data["partial_reason"])
        fallback_chain = data.get("fallback_chain")
        if isinstance(fallback_chain, list):
            safe_chain: list[dict[str, str]] = []
            for item in fallback_chain[:20]:
                if not isinstance(item, Mapping):
                    continue
                safe_chain.append(
                    {
                        "provider": _safe_name(item.get("provider", "")),
                        "model": _safe_name(item.get("model", "")),
                    }
                )
            safe_data["fallback_chain"] = safe_chain
        if data.get("message") is not None:
            safe_data["message"] = redact_sensitive_text(
                str(data["message"]), known_secrets=self.known_secrets
            )[:2_000]
        safe_event = {"event": event_name, "data": safe_data}
        try:
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    """
                    UPDATE web_tasks
                    SET status = ?, terminal_event_json = ?, updated_at_utc = ?
                    WHERE task_id = ?
                    """,
                    (event_name, _json(safe_event), utc_now(), task_id),
                )
                if cursor.rowcount != 1:
                    raise DiagnosticStoreError("Persistent Web task is missing")
        except sqlite3.Error as exc:
            raise DiagnosticStoreError("Cannot persist terminal Web event") from exc

    def task(self, task_id: str) -> dict[str, object] | None:
        """Read one persistent Web task without exposing request contents."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM web_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        terminal = (
            json.loads(str(row["terminal_event_json"]))
            if row["terminal_event_json"]
            else None
        )
        return {
            "task_id": str(row["task_id"]),
            "request_id": row["request_id"],
            "kind": str(row["kind"]),
            "status": str(row["status"]),
            "terminal_event": terminal,
            "created_at_utc": str(row["created_at_utc"]),
            "updated_at_utc": str(row["updated_at_utc"]),
        }

    def recover_interrupted(self) -> tuple[int, int]:
        """Mark crash-left request/task records as interrupted."""

        now = utc_now()
        terminal = {
            "event": "failed",
            "data": {
                "error_type": "process_interrupted",
                "message": "Процесс был остановлен до завершения задачи.",
            },
        }
        with self._lock, self._connection:
            requests = self._connection.execute(
                """
                UPDATE request_attempts
                SET status='interrupted', finished_at_utc=?,
                    error_code='process_restart_or_crash'
                WHERE status='in_progress'
                """,
                (now,),
            ).rowcount
            tasks = self._connection.execute(
                """
                UPDATE web_tasks
                SET status='failed', terminal_event_json=?, updated_at_utc=?
                WHERE status='running'
                """,
                (_json(terminal), now),
            ).rowcount
        return requests, tasks

    def list_requests(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        """Return bounded diagnostic summaries with no query or exception text."""

        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Invalid diagnostics pagination")
        statement = """
            SELECT request_id, parent_request_id, task_id, thread_id,
                   operation_kind, source, app_version, status,
                   created_at_utc, finished_at_utc, duration_ms, query_mode,
                   query_sha256, query_bytes, query_truncated,
                   query_preview,
                   provider_priority_json, provider_attempts_json,
                   rollback_attempted, rollback_success,
                   rollback_checkpoint_rows, rollback_write_rows,
                   filesystem_side_effects, error_code
            FROM request_attempts
        """
        parameters: list[object] = []
        if status:
            statement += " WHERE status = ?"
            parameters.append(status)
        statement += " ORDER BY created_at_utc DESC, rowid DESC LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        with self._lock:
            rows = self._connection.execute(statement, parameters).fetchall()
        return [self._summary(row) for row in rows]

    def request(
        self,
        request_id: str,
        *,
        include_query: bool = False,
    ) -> dict[str, object]:
        """Return one diagnostic record with explicit optional query access."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM request_attempts WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        result = self._summary(row)
        result.update(
            {
                "tool_audit": json.loads(str(row["tool_audit_json"])),
                "exception_chain": json.loads(str(row["exception_chain_json"])),
                "baseline_checkpoint_id": row["baseline_checkpoint_id"],
                "retryable": bool(row["retryable"]),
            }
        )
        if include_query:
            result["query"] = row["query_text"]
            result["query_available"] = row["query_text"] is not None
        return result

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, object]:
        return {
            "request_id": str(row["request_id"]),
            "parent_request_id": row["parent_request_id"],
            "task_id": row["task_id"],
            "thread_id": str(row["thread_id"]),
            "operation_kind": str(row["operation_kind"]),
            "source": str(row["source"]),
            "app_version": str(row["app_version"]),
            "status": str(row["status"]),
            "created_at_utc": str(row["created_at_utc"]),
            "finished_at_utc": row["finished_at_utc"],
            "duration_ms": row["duration_ms"],
            "query_mode": str(row["query_mode"]),
            "query_sha256": str(row["query_sha256"]),
            "query_preview": row["query_preview"],
            "query_bytes": int(row["query_bytes"]),
            "query_truncated": bool(row["query_truncated"]),
            "provider_priority": json.loads(str(row["provider_priority_json"])),
            "provider_attempts": json.loads(str(row["provider_attempts_json"])),
            "rollback_attempted": bool(row["rollback_attempted"]),
            "rollback_success": (
                None
                if row["rollback_success"] is None
                else bool(row["rollback_success"])
            ),
            "rollback_checkpoint_rows": int(row["rollback_checkpoint_rows"]),
            "rollback_write_rows": int(row["rollback_write_rows"]),
            "filesystem_side_effects": bool(row["filesystem_side_effects"]),
            "error_code": row["error_code"],
        }

    def cleanup(self) -> int:
        """Apply age and row-count retention in bounded transactions."""

        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        deleted = 0
        with self._lock, self._connection:
            deleted += self._connection.execute(
                """
                DELETE FROM request_attempts
                WHERE request_id IN (
                    SELECT request_id FROM request_attempts
                    WHERE created_at_utc < ? AND status != 'in_progress'
                    ORDER BY created_at_utc, rowid LIMIT 1000
                )
                """,
                (cutoff,),
            ).rowcount
            deleted += self._connection.execute(
                """
                DELETE FROM request_attempts
                WHERE request_id IN (
                    SELECT request_id FROM request_attempts
                    WHERE status != 'in_progress'
                    ORDER BY created_at_utc DESC, rowid DESC LIMIT -1 OFFSET ?
                )
                """,
                (self.max_rows,),
            ).rowcount
            self._connection.execute(
                """
                DELETE FROM web_tasks
                WHERE task_id IN (
                    SELECT task_id FROM web_tasks
                    WHERE updated_at_utc < ? AND status != 'running'
                    ORDER BY updated_at_utc, rowid LIMIT 1000
                )
                """,
                (cutoff,),
            )
            self._connection.execute(
                """
                DELETE FROM web_tasks
                WHERE task_id IN (
                    SELECT task_id FROM web_tasks
                    WHERE status != 'running'
                    ORDER BY updated_at_utc DESC, rowid DESC LIMIT -1 OFFSET ?
                )
                """,
                (self.max_rows,),
            )
        return deleted

    def purge(
        self,
        *,
        request_id: str | None = None,
        older_than_days: int | None = None,
    ) -> int:
        """Delete an explicitly selected request or bounded age range."""

        if request_id is None and older_than_days is None:
            raise ValueError("Select request_id or older_than_days")
        if older_than_days is not None and older_than_days < 0:
            raise ValueError("older_than_days cannot be negative")
        clauses: list[str] = ["status != 'in_progress'"]
        parameters: list[object] = []
        if request_id is not None:
            clauses.append("request_id = ?")
            parameters.append(request_id)
        if older_than_days is not None:
            cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
            clauses.append("created_at_utc < ?")
            parameters.append(cutoff)
        with self._lock, self._connection:
            return self._connection.execute(
                "DELETE FROM request_attempts WHERE " + " AND ".join(clauses),
                parameters,
            ).rowcount

    def close(self) -> None:
        """Close the journal connection."""

        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> DiagnosticStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def configured_secret_values(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Collect secret values for redaction without returning names or logging them."""

    values = os.environ if environ is None else environ
    secret_names = (
        name
        for name in values
        if name.upper().endswith(("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
    )
    return tuple(
        value
        for name in secret_names
        if (value := values.get(name)) and len(value) >= 6
    )
