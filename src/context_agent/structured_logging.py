"""Bounded rotating JSONL logging for local operator diagnostics."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from context_agent.diagnostics import redact_sensitive_text


class _JsonFormatter(logging.Formatter):
    def __init__(self, known_secrets: tuple[str, ...]) -> None:
        super().__init__()
        self.known_secrets = known_secrets

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "safe_fields", {})
        if not isinstance(fields, dict):
            fields = {}
        safe_fields = {
            str(name)[:100]: redact_sensitive_text(
                str(value), known_secrets=self.known_secrets
            )[:2_000]
            for name, value in list(fields.items())[:50]
        }
        payload: dict[str, Any] = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event_code": str(getattr(record, "event_code", "application_log"))[:100],
            "message": redact_sensitive_text(
                record.getMessage(), known_secrets=self.known_secrets
            )[:2_000],
            "fields": safe_fields,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def configure_structured_logger(
    data_dir: Path,
    *,
    known_secrets: tuple[str, ...] = (),
) -> logging.Logger:
    """Return one process-local rotating JSONL logger for a data directory."""

    log_path = (data_dir / "context-agent-server.jsonl").resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"context_agent.server.{hash(log_path)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(_JsonFormatter(known_secrets))
        logger.addHandler(handler)
    return logger
