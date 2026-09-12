"""Allowlisted execution projection shared by API and durable SSE events."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any


def _code(value: object) -> str:
    text = str(value or "")
    return text if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", text) else "unavailable"


def public_job_details(
    job: Mapping[str, Any], view: Mapping[str, object]
) -> dict[str, object]:
    """Default status is metadata only; full evidence needs its separate endpoint."""
    result: dict[str, object] = {
        name: _code(job.get(name))
        for name in (
            "id",
            "thread_id",
            "task_identity",
            "mode",
            "workflow",
            "status",
            "phase",
            "verification_status",
            "last_error_code",
        )
    }
    for name in ("checkpoint_revision", "created_at", "updated_at", "finished_at"):
        value = job.get(name)
        result[name] = value if isinstance(value, (int, float)) else None
    progress = job.get("progress", {})
    result["progress"] = (
        {
            name: value
            for name, value in progress.items()
            if isinstance(value, (int, float, bool))
            or (
                name in {"status", "phase", "verification_status", "last_error_code"}
                and isinstance(value, str)
                and value == _code(value)
            )
        }
        if isinstance(progress, Mapping)
        else {}
    )
    result["workspace"] = "/workspace"
    result["execution"] = dict(view)
    return result


def execution_view(
    job: Mapping[str, Any],
    task: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Project receipts, not model prose, into a small public status card."""
    task = task or {}
    results = job.get("verification_results", [])
    checks = []
    contexts = {}
    for receipt in results if isinstance(results, list) else []:
        if not isinstance(receipt, Mapping):
            continue
        name = str(receipt.get("check", ""))
        if name not in {
            "ruff_check",
            "ruff_format_check",
            "pytest",
            "mypy",
            "compileall",
        }:
            continue
        checks.append(
            {
                "check": name,
                "status": _code(receipt.get("status", "unavailable")),
                "return_code": receipt.get("return_code")
                if isinstance(receipt.get("return_code"), int)
                else None,
            }
        )
        context = receipt.get("verification_context", {})
        if isinstance(context, Mapping):
            identity = str(context.get("persisted_context_id", ""))
            if re.fullmatch(r"[a-f0-9]{64}", identity):
                contexts[identity] = context
    context = next(iter(contexts.values())) if len(contexts) == 1 else {}
    root = str(context.get("project_root", ""))
    if (
        (root != "/workspace" and not root.startswith("/workspace/"))
        or any(part in {".", ".."} for part in root.split("/"))
        or any(char in root for char in "\\:\r\n")
    ):
        root = ""
    outcome = str(job.get("status", "unavailable"))
    pending = task.get("projection_state") == "pending"
    view: dict[str, object] = {
        "job_id": _code(job.get("id")),
        "saved_task_id": _code(job.get("task_identity")),
        "outcome": _code(outcome),
        "task_status": _code(task.get("status")),
        "projection_state": _code(task.get("projection_state")),
        "display_status": "finalization_pending" if pending else _code(outcome),
        "phase": _code(job.get("phase")),
        "error_code": _code(job["last_error_code"])
        if job.get("last_error_code")
        else "",
        "project_root": root or "unavailable",
        "environment_label": (
            ".venv"
            if context.get("environment_label") == ".venv"
            else "configured"
            if context.get("environment_label") == "configured"
            else "unavailable"
        ),
        "context_id": next(iter(contexts)) if len(contexts) == 1 else "",
        "verification_status": _code(job.get("verification_status", "not_run")),
        "checks": checks,
    }
    timing = {
        "saved_task_revision": task.get("revision"),
        "task_lease_remaining_seconds": task.get("lease_remaining_seconds"),
        "job_lease_remaining_seconds": job.get("job_lease_remaining_seconds"),
        "lease_generation": job.get("lease_generation"),
        "last_heartbeat_at": job.get("last_heartbeat_at"),
        "active_time_seconds": job.get("active_time_seconds"),
    }
    for name, value in timing.items():
        view[name] = (
            value if isinstance(value, (int, float)) and math.isfinite(value) else None
        )
    return view
