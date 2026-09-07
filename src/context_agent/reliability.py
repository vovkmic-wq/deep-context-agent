"""Bounded model observations and cooperative, evidence-aware execution guards."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from context_agent.errors import AgentError


class ExecutionStopped(AgentError):  # noqa: N818 -- internal control-flow signal
    """A deterministic stop that must not trigger model or graph replay."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def repeated_prose(text: str) -> bool:
    """Conservative consecutive paragraph detector, not a semantic-loop oracle."""
    text = text[-32_000:]
    if text.lstrip().startswith(("{", "[")) or "```" in text:
        return False
    paragraphs = re.split(r"\n\s*\n", text)
    previous = ""
    count = 0
    for paragraph in paragraphs:
        current = " ".join(paragraph.split()).casefold()
        if len(current) < 80 or "|" in current:
            count, previous = 0, ""
            continue
        count = count + 1 if current == previous else 1
        if count >= 4:
            return True
        previous = current
    return False


class ReliabilityMiddleware(AgentMiddleware):
    """Capture actual attempts inside retry middleware; never replay tools."""

    name = "execution_reliability"

    def __init__(
        self,
        config: Any,
        journal: Any,
        provider: Callable[[], Any],
        policy: Callable[[], dict[str, Any]],
        evidence: Callable[[], list[Any]],
    ) -> None:
        self.config = config
        self.journal = journal
        self.provider = provider
        self.policy = policy
        self.evidence = evidence
        self.cancelled = threading.Event()
        self.authority: Callable[[], None] = lambda: None
        self.request_id: str | None = None
        self.records: list[dict[str, Any]] = []
        self.start_budget()

    def start_budget(self, *, persistent: bool = False) -> None:
        """Start a one-shot budget or a persistent scheduler session.

        Persistent jobs own their active-time and wall-clock budgets in the durable
        job store.  Reusing the legacy one-shot deadline here would make a sequence
        of successful handoffs expire after ``AGENT_TASK_TIMEOUT_SECONDS``.
        """

        self.persistent = persistent
        self.deadline: float | None = (
            None if persistent else time.monotonic() + self.config.task_timeout_seconds
        )
        self.attempts = 0
        self.stagnant = 0
        self.seen: set[tuple[Any, ...]] = set()
        self.unit_attempt_start = 0
        self.unit_model_call_limit: int | None = None
        self.unit_deadline: float | None = None

    def begin_unit(
        self,
        model_call_limit: int | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        """Start a soft unit budget without resetting task-wide counters."""

        self.unit_attempt_start = self.attempts
        self.unit_model_call_limit = model_call_limit
        self.unit_deadline = (
            time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        )

    def clear_unit(self) -> None:
        """Disable the current unit boundary without resetting the task budget."""

        self.unit_model_call_limit = None
        self.unit_deadline = None

    def check(self) -> None:
        if self.cancelled.is_set():
            raise ExecutionStopped("task_cancelled")
        self.authority()
        now = time.monotonic()
        if self.deadline is not None and now >= self.deadline:
            raise ExecutionStopped("task_deadline")
        if self.unit_deadline is not None and now >= self.unit_deadline:
            raise ExecutionStopped("unit_deadline")

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        self.check()
        return handler(request)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        self.check()
        if (
            self.unit_model_call_limit is not None
            and self.attempts - self.unit_attempt_start >= self.unit_model_call_limit
        ):
            raise ExecutionStopped("soft_yield")
        if self.attempts >= self.config.task_model_attempts:
            raise ExecutionStopped("model_attempt_budget")
        evidence = {
            (e.name, e.path, e.content_sha256, e.result_count, e.result)
            for e in self.evidence()
            if e.status == "success" and e.name not in {"write_todos", "runtime_info"}
        }
        self.stagnant = 0 if evidence - self.seen else self.stagnant + 1
        self.seen.update(evidence)
        if self.stagnant > self.config.no_progress_limit:
            raise ExecutionStopped("no_verified_progress")
        policy = self.policy()
        system = request.system_message.text if request.system_message else ""
        if policy:
            system += "\n<trusted_turn_policy>\n" + json.dumps(
                policy, ensure_ascii=False
            )
            system += "\n</trusted_turn_policy>\n"
        settings = dict(request.model_settings)
        settings["max_tokens"] = self.config.model_output_tokens
        target = self.provider()
        timeout_candidates = [
            float(target.timeout),
            float(self.config.model_turn_timeout_seconds),
        ]
        now = time.monotonic()
        if self.deadline is not None:
            timeout_candidates.append(max(1.0, self.deadline - now))
        if self.unit_deadline is not None:
            timeout_candidates.append(max(1.0, self.unit_deadline - now))
        settings["timeout"] = min(timeout_candidates)
        request = request.override(
            system_message=SystemMessage(content=system),
            model_settings=settings,
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                [system, [m.model_dump(mode="json") for m in request.messages]],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        target = self.provider()
        record: dict[str, Any] = {
            "attempt_id": uuid4().hex,
            "ordinal": len(self.records) + 1,
            "provider": target.name,
            "model": target.model,
            "status": "running",
            "prompt_sha256": fingerprint,
            "max_output_tokens": self.config.model_output_tokens,
            "temperature": settings.get(
                "temperature", getattr(request.model, "temperature", None)
            ),
            "reasoning_effort": settings.get(
                "reasoning_effort", getattr(request.model, "reasoning_effort", None)
            ),
            "input_tokens": None,
            "output_tokens": None,
            "finish_reason": None,
            "stop_reason": None,
            "repetition_detected": False,
            "duration_ms": None,
            "cancellation": "not_requested",
            "retry_count": 0,
            "model_route": policy.get("model_route", {}),
        }
        self.attempts += 1
        self.records.append(record)
        if len(self.records) >= 2:
            previous = self.records[-2]
            if (
                previous["status"] == "error"
                and previous["provider"] == record["provider"]
                and previous["prompt_sha256"] == fingerprint
            ):
                record["retry_count"] = previous["retry_count"] + 1
        self._persist(record)
        started = time.monotonic()
        try:
            response = handler(request)
            messages = getattr(response, "result", [])
            for message in messages:
                usage = getattr(message, "usage_metadata", None) or {}
                metadata = getattr(message, "response_metadata", None) or {}
                for field in ("input_tokens", "output_tokens"):
                    record[field] = usage.get(field)
                for field in ("finish_reason", "stop_reason"):
                    record[field] = metadata.get(field)
                incomplete = metadata.get("incomplete_details") or {}
                if isinstance(incomplete, dict) and incomplete.get("reason"):
                    record["stop_reason"] = incomplete["reason"]
                text = message.content if isinstance(message.content, str) else ""
                repeated = self.config.repetition_mode != "off" and (
                    not getattr(message, "tool_calls", None) and repeated_prose(text)
                )
                record["repetition_detected"] = repeated
                if (
                    metadata.get("finish_reason") in {"length", "max_tokens"}
                    or metadata.get("status") == "incomplete"
                    or record["stop_reason"] in {"max_output_tokens", "max_tokens"}
                ):
                    raise ExecutionStopped("generation_truncated")
                if repeated and self.config.repetition_mode == "enforce":
                    raise ExecutionStopped("generation_repetition")
            elapsed = time.monotonic() - started
            if elapsed >= self.config.model_turn_timeout_seconds:
                raise ExecutionStopped("model_turn_timeout")
            self.check()
            record["status"] = "success"
            return response
        except BaseException as exc:
            record["status"] = "error"
            record["error_type"] = type(exc).__name__
            if isinstance(exc, ExecutionStopped):
                record["stop_reason"] = exc.code
            if self.cancelled.is_set():
                record["cancellation"] = "local_observed_remote_unconfirmed"
            raise
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            self._persist(record)

    def _persist(self, record: dict[str, Any]) -> None:
        if self.request_id is not None:
            self.journal.record_model_attempt(self.request_id, record)
