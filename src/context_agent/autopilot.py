"""Persistent orchestration state for long-running project jobs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"partial", "blocked", "cancelled", "failed", "complete"}
)
_CONTROL_STATUSES: Final[frozenset[str]] = frozenset({"running", "paused", "cancelled"})
_REPORT_LIMIT: Final[int] = 100_000
_SUMMARY_LIMIT: Final[int] = 4_000
_NEXT_OPERATION_LIMIT: Final[int] = 8_000
_NEXT_OPERATION_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "answer",
        "analyze",
        "create_file",
        "edit_file",
        "remove_path",
        "run_checks",
        "read_file",
        "search",
        "produce_plan",
        "return_blocker",
    }
)
_MUTATING_NEXT_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"create_file", "edit_file", "remove_path"}
)
_ALLOWED_PHASE_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "queued": frozenset({"audit", "discover", "implement", "execute"}),
    "audit": frozenset({"audit", "verify", "complete", "blocked"}),
    "discover": frozenset({"discover", "plan", "blocked"}),
    "plan": frozenset({"implement", "blocked", "waiting_user"}),
    "implement": frozenset({"targeted-discovery", "verify", "blocked", "waiting_user"}),
    "targeted-discovery": frozenset({"implement", "blocked"}),
    "verify": frozenset({"repair", "complete", "blocked"}),
    "repair": frozenset({"verify", "blocked"}),
    "execute": frozenset({"execute", "complete", "partial", "blocked"}),
}


@dataclass(frozen=True, slots=True)
class NextOperation:
    """Validated, persistence-safe description of the next bounded action."""

    phase: str
    operation: str
    target: str
    objective: str
    required_evidence_ids: tuple[str, ...] = ()
    expected_effect: str = ""
    verification_commands: tuple[str, ...] = ()
    blocking_conditions: tuple[str, ...] = ()
    contract_version: int = 1
    component: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "operation": self.operation,
            "target": self.target,
            "objective": self.objective,
            "required_evidence_ids": list(self.required_evidence_ids),
            "expected_effect": self.expected_effect,
            "verification_commands": list(self.verification_commands),
            "blocking_conditions": list(self.blocking_conditions),
            "contract_version": self.contract_version,
            "component": self.component,
        }

    @classmethod
    def parse(cls, value: Mapping[str, object], workspace: Path) -> NextOperation:
        phase = str(value.get("phase") or "").strip().casefold()
        operation = str(value.get("operation") or "").strip().casefold()
        target = str(value.get("target") or "").strip()
        objective = str(value.get("objective") or "").strip()
        if phase not in {
            "discover",
            "targeted-discovery",
            "plan",
            "implement",
            "verify",
            "repair",
        }:
            raise ValueError("Unsupported next-operation phase")
        if operation not in _NEXT_OPERATION_ALLOWLIST:
            raise ValueError("Unsupported next-operation operation")
        if not objective or len(objective) > 2_000:
            raise ValueError("Next-operation objective must contain 1..2000 characters")
        if target:
            normalized = target.replace("\\", "/")
            if normalized.startswith("/workspace/"):
                relative = normalized.removeprefix("/workspace/")
                resolved = workspace.joinpath(relative).resolve(strict=False)
            else:
                candidate = Path(target)
                resolved = (
                    candidate.resolve(strict=False)
                    if candidate.is_absolute()
                    else workspace.joinpath(candidate).resolve(strict=False)
                )
            if resolved != workspace and not resolved.is_relative_to(workspace):
                raise ValueError("Next-operation target escapes workspace")
            target = "/workspace" + (
                "/" + resolved.relative_to(workspace).as_posix()
                if resolved != workspace
                else ""
            )
        if operation in _MUTATING_NEXT_OPERATIONS and not target:
            raise ValueError("Mutating next-operation requires a concrete target")

        def short_tuple(name: str, maximum: int, width: int) -> tuple[str, ...]:
            raw = value.get(name, ())
            if not isinstance(raw, (list, tuple)) or len(raw) > maximum:
                raise ValueError(f"Invalid next-operation field: {name}")
            items = tuple(str(item).strip() for item in raw)
            if any(not item or len(item) > width for item in items):
                raise ValueError(f"Invalid next-operation field: {name}")
            return items

        expected_effect = str(value.get("expected_effect") or "").strip()[:1_000]
        verification_commands = short_tuple("verification_commands", 20, 1_000)
        if operation in _MUTATING_NEXT_OPERATIONS and not expected_effect:
            raise ValueError("Mutating next-operation requires an expected effect")
        if operation in _MUTATING_NEXT_OPERATIONS and not verification_commands:
            raise ValueError("Mutating next-operation requires a verification plan")
        raw_contract_version = value.get("contract_version")
        if raw_contract_version is not None and not isinstance(
            raw_contract_version, (str, int)
        ):
            raise ValueError("Invalid next-operation contract version")
        contract_version = int(raw_contract_version or 1)
        if contract_version != 1:
            raise ValueError("Unsupported next-operation contract version")
        return cls(
            phase=phase,
            operation=operation,
            target=target,
            objective=objective,
            required_evidence_ids=short_tuple("required_evidence_ids", 50, 200),
            expected_effect=expected_effect,
            verification_commands=verification_commands,
            blocking_conditions=short_tuple("blocking_conditions", 20, 1_000),
            contract_version=contract_version,
            component=str(value.get("component") or "").strip()[:200],
        )


def _covered_units(intervals: list[tuple[int, int]]) -> int:
    """Return the size of a union of half-open integer intervals."""

    total = 0
    current_start = -1
    current_end = -1
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if current_start < 0:
            current_start, current_end = start, end
        elif start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    if current_start >= 0:
        total += current_end - current_start
    return total


@dataclass(frozen=True, slots=True)
class AutopilotProgress:
    """Stable, JSON-safe progress for one persistent user objective."""

    job_id: str
    status: str
    phase: str
    mode: str
    audit_run_id: str | None
    batch_size: int
    attempts: int
    replans: int
    completed_units: int
    failed_units: int
    verification_status: str
    last_error_code: str | None
    requested_status: str | None
    interrupted_units: int = 0
    lease_generation: int = 0
    last_heartbeat_at: float | None = None
    active_unit_started_at: float | None = None
    workflow: str = "project-audit"
    yielded_units: int = 0
    file_reads: int = 0
    unique_lines_read: int = 0
    changed_files: int = 0
    checks_run: int = 0
    escalation_count: int = 0
    next_operation: str = ""
    next_operation_data: dict[str, object] | None = None
    phase_attempts: int = 0
    checkpoint_revision: int = 0
    model_turn_timeout_seconds: float = 0.0
    unit_timeout_seconds: float = 0.0
    unit_time_seconds: float = 0.0
    unit_time_remaining_seconds: float = 0.0
    active_time_seconds: float = 0.0
    active_time_limit_seconds: float = 0.0
    active_time_remaining_seconds: float = 0.0
    wall_time_seconds: float = 0.0
    wall_time_limit_seconds: float = 0.0
    wall_time_remaining_seconds: float = 0.0
    discovery_units: int = 0
    targeted_discovery_units: int = 0
    discovery_searches: int = 0
    last_progress_kind: str = ""
    last_progress_at: float | None = None
    renewal_reason: str = ""
    blocker: dict[str, object] | None = None

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def as_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "phase": self.phase,
            "mode": self.mode,
            "audit_run_id": self.audit_run_id,
            "batch_size": self.batch_size,
            "attempts": self.attempts,
            "replans": self.replans,
            "completed_units": self.completed_units,
            "failed_units": self.failed_units,
            "verification_status": self.verification_status,
            "last_error_code": self.last_error_code,
            "requested_status": self.requested_status,
            "interrupted_units": self.interrupted_units,
            "lease_generation": self.lease_generation,
            "last_heartbeat_at": self.last_heartbeat_at,
            "active_unit_started_at": self.active_unit_started_at,
            "workflow": self.workflow,
            "yielded_units": self.yielded_units,
            "file_reads": self.file_reads,
            "unique_lines_read": self.unique_lines_read,
            "changed_files": self.changed_files,
            "checks_run": self.checks_run,
            "escalation_count": self.escalation_count,
            "next_operation": self.next_operation,
            "next_operation_data": self.next_operation_data,
            "phase_attempts": self.phase_attempts,
            "checkpoint_revision": self.checkpoint_revision,
            "model_turn_timeout_seconds": self.model_turn_timeout_seconds,
            "unit_timeout_seconds": self.unit_timeout_seconds,
            "unit_time_seconds": round(self.unit_time_seconds, 3),
            "unit_time_remaining_seconds": round(self.unit_time_remaining_seconds, 3),
            "active_time_seconds": round(self.active_time_seconds, 3),
            "active_time_limit_seconds": self.active_time_limit_seconds,
            "active_time_remaining_seconds": round(
                self.active_time_remaining_seconds, 3
            ),
            "wall_time_seconds": round(self.wall_time_seconds, 3),
            "wall_time_limit_seconds": self.wall_time_limit_seconds,
            "wall_time_remaining_seconds": round(self.wall_time_remaining_seconds, 3),
            "discovery_units": self.discovery_units,
            "targeted_discovery_units": self.targeted_discovery_units,
            "discovery_searches": self.discovery_searches,
            "last_progress_kind": self.last_progress_kind,
            "last_progress_at": self.last_progress_at,
            "renewal_reason": self.renewal_reason,
            "blocker": self.blocker,
            "terminal": self.terminal,
        }


@dataclass(frozen=True, slots=True)
class AutopilotLease:
    """Exclusive bounded lease used by one controller process."""

    job_id: str
    token: str
    generation: int


class AutopilotLeaseError(RuntimeError):
    """Raised when a controller no longer owns the current job generation."""


class AutopilotRevisionError(RuntimeError):
    """Raised when an operator command targets a stale job revision."""


class AutopilotHeartbeat:
    """Renew one job lease while a bounded work unit is executing."""

    def __init__(
        self,
        store: AutopilotStore,
        lease: AutopilotLease,
        *,
        lease_seconds: int,
        interval_seconds: float,
        unit_id: str | None = None,
        deadline_seconds: float | None = None,
        on_heartbeat: Callable[[bool], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Heartbeat interval must be positive")
        self._store = store
        self._lease = lease
        self._lease_seconds = lease_seconds
        self._interval_seconds = min(
            interval_seconds,
            max(0.1, lease_seconds / 3),
        )
        self._unit_id = unit_id
        self._deadline_seconds = deadline_seconds
        self._on_heartbeat = on_heartbeat
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._deadline_exceeded = threading.Event()
        self._error: AutopilotLeaseError | None = None
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._run,
            name=f"autopilot-heartbeat-{lease.job_id[:8]}",
            daemon=True,
        )

    @property
    def deadline_exceeded(self) -> bool:
        return self._deadline_exceeded.is_set()

    @property
    def lease_lost(self) -> bool:
        return self._lost.is_set()

    def __enter__(self) -> AutopilotHeartbeat:
        self._store.renew_lease(
            self._lease,
            self._lease_seconds,
            unit_id=self._unit_id,
        )
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._interval_seconds + 0.5))

    def ensure_owned(self) -> None:
        """Fail closed before the caller commits work-unit state."""

        if self._error is not None:
            raise self._error
        self._store.assert_lease(self._lease)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            elapsed = time.monotonic() - self._started_at
            deadline_exceeded = bool(
                self._deadline_seconds is not None and elapsed >= self._deadline_seconds
            )
            if deadline_exceeded:
                self._deadline_exceeded.set()
            try:
                self._store.renew_lease(
                    self._lease,
                    self._lease_seconds,
                    unit_id=self._unit_id,
                )
            except AutopilotLeaseError as exc:
                self._error = exc
                self._lost.set()
                return
            if self._on_heartbeat is not None:
                try:
                    self._on_heartbeat(deadline_exceeded)
                except Exception:
                    # UI/report callbacks must never stop the ownership heartbeat.
                    continue


class AutopilotStore:
    """Persist jobs and independently restartable model work units."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
            timeout=30,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._unit_monotonic_starts: dict[str, float] = {}
        self._initialize_schema()
        self.recover_expired()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> AutopilotStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def recover_expired(self) -> int:
        """Pause expired jobs and preserve their unfinished units as interrupted."""

        now = time.time()
        with self._lock, self._connection:
            rows = self._connection.execute(
                """
                SELECT id FROM autopilot_jobs
                WHERE status = 'running' AND lease_token IS NOT NULL
                    AND COALESCE(lease_until, 0) <= ?
                """,
                (now,),
            ).fetchall()
            job_ids = [str(row["id"]) for row in rows]
            for job_id in job_ids:
                active = self._connection.execute(
                    "SELECT started_at, last_heartbeat_at FROM autopilot_work_units "
                    "WHERE job_id = ? AND status = 'running' "
                    "ORDER BY sequence DESC LIMIT 1",
                    (job_id,),
                ).fetchone()
                active_delta = (
                    max(
                        0.0,
                        float(active["last_heartbeat_at"] or active["started_at"])
                        - float(active["started_at"]),
                    )
                    if active is not None and active["started_at"] is not None
                    else 0.0
                )
                self._connection.execute(
                    """
                    UPDATE autopilot_work_units
                    SET status = 'interrupted',
                        error_code = COALESCE(error_code, 'worker_interrupted'),
                        summary = CASE WHEN summary = '' THEN
                            'Controller lease expired before unit commit.'
                            ELSE summary END,
                        finished_at = COALESCE(finished_at, ?)
                    WHERE job_id = ? AND status = 'running'
                    """,
                    (now, job_id),
                )
                self._connection.execute(
                    """
                    UPDATE autopilot_jobs
                    SET status = 'paused', resume_phase = phase,
                        phase = 'interrupted',
                        last_error_code = 'autopilot_lease_expired',
                        last_error_message =
                            'Controller lease expired; resume is safe.',
                        lease_token = NULL, lease_until = NULL,
                        active_time_seconds = active_time_seconds + ?,
                        updated_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (active_delta, now, job_id),
                )
        return len(job_ids)

    def enqueue(
        self,
        *,
        thread_id: str,
        objective: str,
        workspace: Path,
        allow_write: bool,
        batch_size: int,
        include_patterns: tuple[str, ...] = (),
        exclude_patterns: tuple[str, ...] = (),
        workflow: str = "project-audit",
        task_identity: str | None = None,
        model_turn_timeout_seconds: int = 180,
        unit_timeout_seconds: int = 900,
        active_time_limit_seconds: int = 14_400,
        wall_time_limit_seconds: int = 86_400,
    ) -> str:
        """Durably enqueue a job before an HTTP caller receives its identifier."""

        job_id = self.job_id_for(
            thread_id=thread_id,
            objective=objective,
            workspace=workspace,
            allow_write=allow_write,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            workflow=workflow,
            task_identity=task_identity,
        )
        now = time.time()
        initial_phase = (
            "audit"
            if workflow == "project-audit"
            else "discover"
            if workflow in {"project-change", "project-test"}
            else "implement"
            if workflow == "targeted-change"
            else "execute"
        )
        mode = "allow-write" if allow_write else "read-only"
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO autopilot_jobs(
                    id, thread_id, objective, objective_sha256, workspace, mode,
                    workflow, task_identity, include_patterns, exclude_patterns,
                    status, phase,
                    batch_size, model_turn_timeout_seconds, unit_timeout_seconds,
                    active_time_limit_seconds,
                    wall_time_limit_seconds, wall_deadline_at, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(id) DO UPDATE SET
                    status = CASE
                        WHEN autopilot_jobs.status IN (
                            'running', 'complete', 'cancelled'
                        )
                            THEN autopilot_jobs.status
                        ELSE 'queued'
                    END,
                    control_requested = CASE
                        WHEN autopilot_jobs.status = 'running'
                            THEN autopilot_jobs.control_requested
                        ELSE NULL
                    END,
                    updated_at = excluded.updated_at,
                    finished_at = CASE
                        WHEN autopilot_jobs.status IN ('complete', 'cancelled')
                            THEN autopilot_jobs.finished_at
                        ELSE NULL
                    END
                """,
                (
                    job_id,
                    thread_id.strip(),
                    objective.strip(),
                    hashlib.sha256(objective.strip().encode("utf-8")).hexdigest(),
                    str(workspace.resolve()),
                    mode,
                    workflow,
                    task_identity,
                    json.dumps(include_patterns, ensure_ascii=False),
                    json.dumps(exclude_patterns, ensure_ascii=False),
                    initial_phase,
                    batch_size,
                    model_turn_timeout_seconds,
                    unit_timeout_seconds,
                    active_time_limit_seconds,
                    wall_time_limit_seconds,
                    now + wall_time_limit_seconds,
                    now,
                    now,
                ),
            )
        return job_id

    def resumable_jobs(self, *, workspace: Path) -> list[dict[str, object]]:
        """Return queued or crash-interrupted jobs eligible for reconciliation."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM autopilot_jobs
                WHERE workspace = ? AND (
                    status = 'queued' OR (
                        status = 'paused'
                        AND last_error_code = 'autopilot_lease_expired'
                    )
                )
                ORDER BY created_at
                """,
                (str(workspace.resolve()),),
            ).fetchall()
        return [dict(row) for row in rows]

    def start_or_resume(
        self,
        *,
        thread_id: str,
        objective: str,
        workspace: Path,
        allow_write: bool,
        batch_size: int,
        include_patterns: tuple[str, ...] = (),
        exclude_patterns: tuple[str, ...] = (),
        lease_seconds: int = 900,
        workflow: str = "project-audit",
        task_identity: str | None = None,
        model_turn_timeout_seconds: int = 180,
        unit_timeout_seconds: int = 900,
        active_time_limit_seconds: int = 14_400,
        wall_time_limit_seconds: int = 86_400,
    ) -> tuple[AutopilotProgress, AutopilotLease]:
        """Create/resume a stable identity and claim an exclusive lease."""

        if not thread_id.strip():
            raise ValueError("thread_id cannot be empty")
        if not objective.strip():
            raise ValueError("Autopilot objective cannot be empty")
        if not 1 <= batch_size <= 25:
            raise ValueError("Autopilot batch size must be between 1 and 25")
        if (
            model_turn_timeout_seconds <= 0
            or unit_timeout_seconds <= 0
            or active_time_limit_seconds <= 0
            or wall_time_limit_seconds <= 0
        ):
            raise ValueError("Autopilot time budgets must be positive")
        if workflow not in {
            "answer",
            "log-analysis",
            "targeted-review",
            "targeted-change",
            "project-audit",
            "project-change",
            "project-test",
        }:
            raise ValueError("Unsupported Autopilot workflow")
        mode = "allow-write" if allow_write else "read-only"
        resolved_workspace = workspace.resolve()
        job_id = self.job_id_for(
            thread_id=thread_id,
            objective=objective,
            workspace=resolved_workspace,
            allow_write=allow_write,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            workflow=workflow,
            task_identity=task_identity,
        )
        token = uuid4().hex
        now = time.time()
        lease_until = now + lease_seconds
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT status, phase, resume_phase, lease_token, lease_until, "
                    "lease_generation, wall_deadline_at "
                    "FROM autopilot_jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if (
                    existing is not None
                    and str(existing["status"]) == "running"
                    and float(existing["lease_until"] or 0) > now
                    and existing["lease_token"]
                ):
                    raise RuntimeError("Autopilot job is already running")
                if existing is not None and str(existing["status"]) == "complete":
                    generation = int(existing["lease_generation"] or 0)
                    self._connection.commit()
                    return self.progress(job_id), AutopilotLease(
                        job_id,
                        "",
                        generation,
                    )
                generation = (
                    int(existing["lease_generation"] or 0) + 1
                    if existing is not None
                    else 1
                )
                if existing is not None:
                    self._connection.execute(
                        """
                        UPDATE autopilot_work_units
                        SET status = 'interrupted',
                            error_code = COALESCE(error_code, 'worker_interrupted'),
                            summary = CASE WHEN summary = '' THEN
                                'Previous controller was interrupted.'
                                ELSE summary END,
                            finished_at = COALESCE(finished_at, ?)
                        WHERE job_id = ? AND status = 'running'
                        """,
                        (now, job_id),
                    )
                initial_phase = (
                    "audit"
                    if workflow == "project-audit"
                    else "discover"
                    if workflow in {"project-change", "project-test"}
                    else "implement"
                    if workflow == "targeted-change"
                    else "execute"
                )
                self._connection.execute(
                    """
                    INSERT INTO autopilot_jobs(
                        id, thread_id, objective, objective_sha256, workspace, mode,
                        workflow,
                        include_patterns, exclude_patterns,
                        status, phase, audit_run_id, batch_size, attempts, replans,
                        verification_status, verification_results, last_error_code,
                        last_error_message, report, control_requested,
                        lease_token, lease_until, lease_generation, last_heartbeat_at,
                        created_at, updated_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, NULL, ?, 0, 0,
                              'not_run', '[]', NULL, NULL, '', NULL, ?, ?, ?, ?,
                              ?, ?, NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        status = 'running',
                        mode = excluded.mode,
                        phase = CASE
                            WHEN autopilot_jobs.phase = 'complete' THEN 'complete'
                            ELSE autopilot_jobs.phase
                        END,
                        batch_size = MIN(
                            autopilot_jobs.batch_size, excluded.batch_size
                        ),
                        lease_token = excluded.lease_token,
                        lease_until = excluded.lease_until,
                        lease_generation = excluded.lease_generation,
                        last_heartbeat_at = excluded.last_heartbeat_at,
                        last_error_code = NULL,
                        last_error_message = NULL,
                        control_requested = NULL,
                        updated_at = excluded.updated_at,
                        finished_at = NULL
                    """,
                    (
                        job_id,
                        thread_id.strip(),
                        objective.strip(),
                        hashlib.sha256(objective.strip().encode("utf-8")).hexdigest(),
                        str(resolved_workspace),
                        mode,
                        workflow,
                        json.dumps(include_patterns, ensure_ascii=False),
                        json.dumps(exclude_patterns, ensure_ascii=False),
                        initial_phase,
                        batch_size,
                        token,
                        lease_until,
                        generation,
                        now,
                        now,
                        now,
                    ),
                )
                resume_phase = (
                    str(existing["resume_phase"] or "") if existing is not None else ""
                )
                if existing is not None and str(existing["phase"]) in {
                    "interrupted",
                    "paused",
                    "partial",
                    "blocked",
                    "failed",
                }:
                    self._connection.execute(
                        "UPDATE autopilot_jobs SET phase = ?, resume_phase = '' "
                        "WHERE id = ?",
                        (resume_phase or initial_phase, job_id),
                    )
                self._connection.execute(
                    """
                    UPDATE autopilot_jobs
                    SET model_turn_timeout_seconds = ?, unit_timeout_seconds = ?,
                        active_time_limit_seconds = ?, wall_time_limit_seconds = ?,
                        wall_deadline_at = COALESCE(wall_deadline_at, ?),
                        task_identity = COALESCE(task_identity, ?)
                    WHERE id = ?
                    """,
                    (
                        model_turn_timeout_seconds,
                        unit_timeout_seconds,
                        active_time_limit_seconds,
                        wall_time_limit_seconds,
                        now + wall_time_limit_seconds,
                        task_identity,
                        job_id,
                    ),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self.progress(job_id), AutopilotLease(job_id, token, generation)

    @staticmethod
    def job_id_for(
        *,
        thread_id: str,
        objective: str,
        workspace: Path,
        allow_write: bool,
        include_patterns: tuple[str, ...] = (),
        exclude_patterns: tuple[str, ...] = (),
        workflow: str = "project-audit",
        task_identity: str | None = None,
    ) -> str:
        """Return the stable identity shared by CLI, Web, and the store."""

        mode = "allow-write" if allow_write else "read-only"
        if task_identity is not None:
            identity = "\0".join(
                (
                    "saved-task-v2",
                    str(workspace.resolve()).casefold(),
                    thread_id.strip(),
                    task_identity,
                    workflow,
                )
            )
            return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        identity_parts = [
            "deep-context-autopilot-v1",
            str(workspace.resolve()).casefold(),
            thread_id.strip(),
            objective.strip(),
            mode,
            json.dumps(include_patterns, ensure_ascii=False),
            json.dumps(exclude_patterns, ensure_ascii=False),
        ]
        if workflow != "project-audit":
            identity_parts.append(workflow)
        identity = "\0".join(identity_parts)
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    def renew_lease(
        self,
        lease: AutopilotLease,
        lease_seconds: int = 900,
        *,
        unit_id: str | None = None,
    ) -> None:
        if not lease.token:
            return
        now = time.time()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE autopilot_jobs SET lease_until = ?, last_heartbeat_at = ?,
                    updated_at = ?
                WHERE id = ? AND lease_token = ? AND lease_generation = ?
                    AND status = 'running' AND lease_until > ?
                """,
                (
                    now + lease_seconds,
                    now,
                    now,
                    lease.job_id,
                    lease.token,
                    lease.generation,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                raise AutopilotLeaseError("Autopilot job lease was lost")
            if unit_id is not None:
                unit_cursor = self._connection.execute(
                    """
                    UPDATE autopilot_work_units SET last_heartbeat_at = ?
                    WHERE id = ? AND job_id = ? AND lease_generation = ?
                        AND status = 'running'
                    """,
                    (now, unit_id, lease.job_id, lease.generation),
                )
                if unit_cursor.rowcount == 0:
                    raise AutopilotLeaseError("Autopilot work unit lease was lost")

    def assert_lease(self, lease: AutopilotLease) -> None:
        """Assert ownership for a controller or a mutating tool guard."""

        with self._lock:
            self._require_lease(lease)

    def set_audit_run(self, lease: AutopilotLease, run_id: str) -> None:
        self._leased_update(
            lease,
            "audit_run_id = ?, updated_at = ?",
            (run_id, time.time()),
        )

    def begin_unit(
        self,
        lease: AutopilotLease,
        *,
        phase: str,
        batch_size: int,
        deadline_seconds: int | None = None,
    ) -> tuple[str, int, str]:
        """Create one independently retryable unit and return its worker thread."""

        with self._lock, self._connection:
            self._require_lease(lease)
            sequence = int(
                self._connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 "
                    "FROM autopilot_work_units WHERE job_id = ?",
                    (lease.job_id,),
                ).fetchone()[0]
            )
            unit_id = uuid4().hex
            row = self._connection.execute(
                "SELECT thread_id FROM autopilot_jobs WHERE id = ?",
                (lease.job_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown autopilot job: {lease.job_id}")
            worker_thread_id = (
                f"{row['thread_id']!s}:job:{lease.job_id}:unit:{sequence}"
            )
            now = time.time()
            self._unit_monotonic_starts[unit_id] = time.monotonic()
            self._connection.execute(
                """
                INSERT INTO autopilot_work_units(
                    id, job_id, sequence, phase, status, batch_size,
                    worker_thread_id, lease_generation, error_code, summary,
                    created_at, started_at, last_heartbeat_at, deadline_at,
                    finished_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, NULL, '', ?, ?, ?, ?, NULL)
                """,
                (
                    unit_id,
                    lease.job_id,
                    sequence,
                    phase,
                    batch_size,
                    worker_thread_id,
                    lease.generation,
                    now,
                    now,
                    now,
                    now + deadline_seconds if deadline_seconds else None,
                ),
            )
            self._connection.execute(
                """
                UPDATE autopilot_jobs
                SET attempts = attempts + 1, phase = ?, updated_at = ?
                WHERE id = ? AND lease_token = ? AND lease_generation = ?
                """,
                (phase, now, lease.job_id, lease.token, lease.generation),
            )
        return unit_id, sequence, worker_thread_id

    def complete_unit(self, lease: AutopilotLease, unit_id: str, summary: str) -> None:
        self._finish_unit(lease, unit_id, "complete", None, summary)

    def yield_unit(self, lease: AutopilotLease, unit_id: str, summary: str) -> None:
        """Commit a planned handoff without counting it as a failed unit."""

        self._finish_unit(lease, unit_id, "yielded", "soft_yield", summary)

    def set_next_operation(
        self,
        lease: AutopilotLease,
        operation: str | Mapping[str, object] | NextOperation,
    ) -> None:
        """Persist a validated executable operation while retaining legacy text."""

        if isinstance(operation, NextOperation):
            parsed = operation
        elif isinstance(operation, Mapping):
            with self._lock:
                row = self._connection.execute(
                    "SELECT workspace FROM autopilot_jobs WHERE id = ?",
                    (lease.job_id,),
                ).fetchone()
            if row is None:
                raise ValueError(f"Unknown autopilot job: {lease.job_id}")
            parsed = NextOperation.parse(operation, Path(str(row["workspace"])))
        else:
            parsed = None
        operation_text = (
            parsed.objective if parsed is not None else str(operation).strip()
        )
        operation_json = (
            json.dumps(parsed.as_dict(), ensure_ascii=False, sort_keys=True)
            if parsed is not None
            else "{}"
        )
        self._leased_update(
            lease,
            "next_operation = ?, next_operation_json = ?, "
            "checkpoint_revision = checkpoint_revision + 1, updated_at = ?",
            (
                operation_text[:1_000],
                operation_json[:_NEXT_OPERATION_LIMIT],
                time.time(),
            ),
        )

    def transition_phase(
        self,
        lease: AutopilotLease,
        phase: str,
        *,
        reason: str,
    ) -> AutopilotProgress:
        """Record one explicit scheduler transition without changing authority."""

        normalized = phase.strip().casefold()
        if normalized not in {
            "queued",
            "discover",
            "targeted-discovery",
            "plan",
            "implement",
            "verify",
            "repair",
            "waiting_user",
            "paused",
            "blocked",
            "cancelled",
            "failed",
            "interrupted",
            "complete",
            "audit",
            "execute",
        }:
            raise ValueError("Unsupported autopilot phase")
        now = time.time()
        with self._lock, self._connection:
            self._require_lease(lease)
            row = self._connection.execute(
                "SELECT phase FROM autopilot_jobs WHERE id = ?",
                (lease.job_id,),
            ).fetchone()
            current = str(row["phase"]) if row is not None else ""
            allowed = _ALLOWED_PHASE_TRANSITIONS.get(current)
            if allowed is not None and normalized not in allowed:
                raise ValueError(
                    f"Invalid autopilot phase transition: {current} -> {normalized}"
                )
            cursor = self._connection.execute(
                "UPDATE autopilot_jobs SET phase = ?, renewal_reason = ?, "
                "checkpoint_revision = checkpoint_revision + 1, updated_at = ? "
                "WHERE id = ? AND lease_token = ? AND lease_generation = ?",
                (
                    normalized,
                    reason[:500],
                    now,
                    lease.job_id,
                    lease.token,
                    lease.generation,
                ),
            )
            if cursor.rowcount != 1:
                raise AutopilotLeaseError("Autopilot job lease ownership was lost")
            self._connection.execute(
                "INSERT INTO autopilot_phase_transitions("
                "id, job_id, from_phase, to_phase, reason, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (uuid4().hex, lease.job_id, current, normalized, reason[:500], now),
            )
        return self.progress(lease.job_id)

    def budget_error(self, job_id: str) -> str | None:
        """Return the current durable task-budget terminal code, if any."""

        with self._lock:
            row = self._connection.execute(
                "SELECT active_time_seconds, active_time_limit_seconds, "
                "wall_deadline_at FROM autopilot_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown autopilot job: {job_id}")
        if row["wall_deadline_at"] is not None and time.time() >= float(
            row["wall_deadline_at"]
        ):
            return "task_wall_time_exhausted"
        if float(row["active_time_seconds"] or 0) >= float(
            row["active_time_limit_seconds"] or 0
        ):
            return "task_active_time_exhausted"
        return None

    def record_tool_receipt(
        self,
        lease: AutopilotLease,
        unit_id: str,
        receipt: dict[str, object],
    ) -> None:
        """Durably store one sanitized runtime-owned tool receipt."""

        receipt_name = str(receipt.get("name") or "unknown")[:100]
        receipt_status = str(receipt.get("status") or "unknown")[:50]
        target = str(receipt.get("path") or "")[:2_000]
        evidence_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "operation": receipt_name,
                    "target": target,
                    "status": receipt_status,
                    "content_version": str(receipt.get("content_version") or "")[:200],
                    "before_sha256": str(receipt.get("before_sha256") or "")[:64],
                    "after_sha256": str(receipt.get("content_sha256") or "")[:64],
                    "start_line": receipt.get("start_line"),
                    "end_line": receipt.get("end_line"),
                    "result_count": receipt.get("result_count"),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        with self._lock, self._connection:
            self._require_lease(lease)
            active = self._connection.execute(
                "SELECT 1 FROM autopilot_work_units WHERE id = ? AND job_id = ? "
                "AND lease_generation = ? AND status = 'running'",
                (unit_id, lease.job_id, lease.generation),
            ).fetchone()
            if active is None:
                raise AutopilotLeaseError("Autopilot work unit is not active")
            duplicate = self._connection.execute(
                "SELECT 1 FROM autopilot_tool_receipts WHERE job_id = ? "
                "AND evidence_fingerprint = ? LIMIT 1",
                (lease.job_id, evidence_fingerprint),
            ).fetchone()
            progress_kind = ""
            if receipt_status == "success" and duplicate is None:
                if receipt_name in {
                    "write_file",
                    "edit_file",
                    "make_directory",
                    "remove_path",
                }:
                    progress_kind = "implementation"
                elif receipt_name == "run_project_checks":
                    progress_kind = "verification"
                elif receipt_name in {
                    "read_file",
                    "glob",
                    "grep",
                    "search_context",
                    "list_context_sources",
                }:
                    progress_kind = "information"
            ordinal = int(
                self._connection.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM autopilot_tool_receipts "
                    "WHERE unit_id = ?",
                    (unit_id,),
                ).fetchone()[0]
            )
            self._connection.execute(
                """
                INSERT INTO autopilot_tool_receipts(
                    id, job_id, unit_id, ordinal, operation, target, status,
                    content_version, before_sha256, after_sha256,
                    requested_offset, requested_limit, start_line, end_line,
                    next_cursor, bytes_returned, result_count,
                    evidence_fingerprint, verified_progress, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    lease.job_id,
                    unit_id,
                    ordinal,
                    receipt_name,
                    target,
                    receipt_status,
                    str(receipt.get("content_version") or "")[:200],
                    str(receipt.get("before_sha256") or "")[:64],
                    str(receipt.get("content_sha256") or "")[:64],
                    receipt.get("requested_offset"),
                    receipt.get("requested_limit"),
                    receipt.get("start_line"),
                    receipt.get("end_line"),
                    receipt.get("next_offset"),
                    receipt.get("bytes_returned"),
                    receipt.get("result_count"),
                    evidence_fingerprint,
                    int(bool(progress_kind)),
                    time.time(),
                ),
            )
            self._connection.execute(
                "UPDATE autopilot_jobs SET checkpoint_revision = "
                "checkpoint_revision + 1, last_progress_kind = CASE "
                "WHEN ? != '' THEN ? ELSE last_progress_kind END, "
                "last_progress_at = CASE WHEN ? != '' THEN ? "
                "ELSE last_progress_at END, "
                "updated_at = ? WHERE id = ?",
                (
                    progress_kind,
                    progress_kind,
                    progress_kind,
                    time.time(),
                    time.time(),
                    lease.job_id,
                ),
            )

    def record_escalation(
        self,
        lease: AutopilotLease,
        *,
        previous_provider: str,
        previous_model: str,
        provider: str,
        model: str,
        reason: str,
    ) -> AutopilotProgress:
        """Persist an explainable execution escalation without credentials."""

        with self._lock:
            self._require_lease(lease)
            row = self._connection.execute(
                "SELECT escalation_history FROM autopilot_jobs WHERE id = ?",
                (lease.job_id,),
            ).fetchone()
            try:
                history = json.loads(str(row["escalation_history"] or "[]"))
            except (json.JSONDecodeError, TypeError):
                history = []
        history.append(
            {
                "previous_provider": previous_provider[:64],
                "previous_model": previous_model[:200],
                "provider": provider[:64],
                "model": model[:200],
                "reason": reason[:200],
                "created_at": time.time(),
            }
        )
        payload = json.dumps(history[-10:], ensure_ascii=False, sort_keys=True)
        self._leased_update(
            lease,
            "escalation_count = escalation_count + 1, escalation_history = ?, "
            "updated_at = ?",
            (payload, time.time()),
        )
        return self.progress(lease.job_id)

    def fail_unit(
        self,
        lease: AutopilotLease,
        unit_id: str,
        *,
        error_code: str,
        summary: str,
    ) -> None:
        self._finish_unit(lease, unit_id, "failed", error_code, summary)

    def replan(
        self,
        lease: AutopilotLease,
        *,
        batch_size: int,
        error_code: str,
        safe_message: str,
    ) -> AutopilotProgress:
        self._leased_update(
            lease,
            """
            batch_size = ?, replans = replans + 1,
            last_error_code = ?, last_error_message = ?, updated_at = ?
            """,
            (
                batch_size,
                error_code[:100],
                safe_message[:_SUMMARY_LIMIT],
                time.time(),
            ),
        )
        return self.progress(lease.job_id)

    def record_verification(
        self,
        lease: AutopilotLease,
        *,
        status: str,
        results: list[dict[str, object]],
    ) -> AutopilotProgress:
        if status not in {"passed", "failed", "not_run"}:
            raise ValueError("Unsupported verification status")
        payload = json.dumps(results, ensure_ascii=False, sort_keys=True)[:50_000]
        self._leased_update(
            lease,
            "verification_status = ?, verification_results = ?, updated_at = ?",
            (status, payload, time.time()),
        )
        return self.progress(lease.job_id)

    def mark_complete(self, lease: AutopilotLease, report: str) -> AutopilotProgress:
        now = time.time()
        self._leased_update(
            lease,
            """
            status = 'complete', phase = 'complete', report = ?,
            last_error_code = NULL, last_error_message = NULL,
            blocker_json = '{}',
            lease_token = NULL, lease_until = NULL,
            updated_at = ?, finished_at = ?
            """,
            (report[:_REPORT_LIMIT], now, now),
        )
        return self.progress(lease.job_id)

    def mark_partial(self, lease: AutopilotLease, report: str) -> AutopilotProgress:
        """A finished worker is resumable work, not a verified complete objective."""
        self._leased_update(
            lease,
            "status='partial', resume_phase=phase, phase='partial', report=?, "
            "lease_token=NULL, lease_until=NULL, "
            "updated_at=?, finished_at=?",
            (report[:_REPORT_LIMIT], time.time(), time.time()),
        )
        return self.progress(lease.job_id)

    def mark_blocked(
        self,
        lease: AutopilotLease,
        *,
        error_code: str,
        safe_message: str,
        report: str = "",
        blocker: Mapping[str, object] | None = None,
    ) -> AutopilotProgress:
        blocker_payload = json.dumps(
            dict(blocker or {}), ensure_ascii=False, sort_keys=True
        )[:_NEXT_OPERATION_LIMIT]
        now = time.time()
        self._leased_update(
            lease,
            """
            status = 'blocked', resume_phase = phase, phase = 'blocked',
            last_error_code = ?,
            last_error_message = ?, report = ?, blocker_json = ?, lease_token = NULL,
            lease_until = NULL, updated_at = ?, finished_at = ?
            """,
            (
                error_code[:100],
                safe_message[:_SUMMARY_LIMIT],
                report[:_REPORT_LIMIT],
                blocker_payload,
                now,
                now,
            ),
        )
        return self.progress(lease.job_id)

    def set_control_status(
        self,
        job_id: str,
        status: str,
        *,
        expected_revision: int | None = None,
    ) -> AutopilotProgress:
        if status not in _CONTROL_STATUSES:
            raise ValueError("Unsupported autopilot control status")
        now = time.time()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status, phase, resume_phase, lease_token, lease_until, "
                "checkpoint_revision "
                "FROM autopilot_jobs "
                "WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown autopilot job: {job_id}")
            if expected_revision is not None and int(
                row["checkpoint_revision"] or 0
            ) != int(expected_revision):
                raise AutopilotRevisionError("Autopilot job revision is stale")
            if str(row["status"]) == "complete":
                return self.progress(job_id)
            active_lease = (
                bool(row["lease_token"]) and float(row["lease_until"] or 0) > now
            )
            if active_lease:
                self._connection.execute(
                    """
                    UPDATE autopilot_jobs
                    SET control_requested = ?, checkpoint_revision =
                        checkpoint_revision + 1, updated_at = ? WHERE id = ?
                    """,
                    (status, now, job_id),
                )
            else:
                finished_at = now if status == "cancelled" else None
                stored_status = "queued" if status == "running" else status
                stored_phase = (
                    str(row["resume_phase"] or "discover")
                    if status == "running"
                    else status
                    if status in {"paused", "cancelled"}
                    else str(row["phase"])
                )
                resume_phase = (
                    str(row["phase"])
                    if status == "paused"
                    and str(row["phase"])
                    not in {"paused", "blocked", "partial", "interrupted"}
                    else str(row["resume_phase"] or "")
                )
                self._connection.execute(
                    """
                    UPDATE autopilot_jobs SET status = ?, phase = ?,
                        resume_phase = ?, control_requested = NULL,
                        lease_token = NULL, lease_until = NULL,
                        checkpoint_revision = checkpoint_revision + 1,
                        updated_at = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (
                        stored_status,
                        stored_phase,
                        resume_phase,
                        now,
                        finished_at,
                        job_id,
                    ),
                )
            if not active_lease and status in {"paused", "cancelled"}:
                self._connection.execute(
                    """
                    UPDATE autopilot_work_units
                    SET status = 'interrupted',
                        error_code = COALESCE(error_code, 'worker_interrupted'),
                        summary = CASE WHEN summary = '' THEN
                            'Controller stopped before committing this unit.'
                            ELSE summary END,
                        finished_at = COALESCE(finished_at, ?)
                    WHERE job_id = ? AND status = 'running'
                    """,
                    (now, job_id),
                )
        return self.progress(job_id)

    def honor_requested_control(self, lease: AutopilotLease) -> AutopilotProgress:
        """Apply pause/cancel requested while the current unit was running."""

        with self._lock, self._connection:
            self._require_lease(lease)
            row = self._connection.execute(
                "SELECT control_requested FROM autopilot_jobs WHERE id = ?",
                (lease.job_id,),
            ).fetchone()
            requested = str(row["control_requested"] or "") if row else ""
            if requested not in {"paused", "cancelled"}:
                return self.progress(lease.job_id)
            now = time.time()
            self._connection.execute(
                """
                UPDATE autopilot_jobs
                SET status = ?, resume_phase = CASE
                        WHEN ? = 'paused' THEN phase ELSE resume_phase END,
                    phase = ?, control_requested = NULL,
                    lease_token = NULL, lease_until = NULL,
                    updated_at = ?, finished_at = ?
                WHERE id = ? AND lease_token = ? AND lease_generation = ?
                """,
                (
                    requested,
                    requested,
                    requested,
                    now,
                    now if requested == "cancelled" else None,
                    lease.job_id,
                    lease.token,
                    lease.generation,
                ),
            )
        return self.progress(lease.job_id)

    def progress(self, job_id: str) -> AutopilotProgress:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id, status, phase, mode, audit_run_id, batch_size,
                       attempts, replans, verification_status, last_error_code,
                       control_requested, lease_generation, last_heartbeat_at,
                       workflow, escalation_count, next_operation,
                       next_operation_json, checkpoint_revision,
                       model_turn_timeout_seconds, unit_timeout_seconds,
                       active_time_seconds, active_time_limit_seconds,
                       wall_time_limit_seconds, wall_deadline_at, created_at,
                       last_progress_kind, last_progress_at, renewal_reason,
                       blocker_json
                FROM autopilot_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown autopilot job: {job_id}")
            counts = {
                str(item["status"]): int(item["count"])
                for item in self._connection.execute(
                    """
                    SELECT status, COUNT(*) AS count FROM autopilot_work_units
                    WHERE job_id = ? GROUP BY status
                    """,
                    (job_id,),
                ).fetchall()
            }
            active = self._connection.execute(
                """
                SELECT started_at, deadline_at FROM autopilot_work_units
                WHERE job_id = ? AND status = 'running'
                ORDER BY sequence DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            phase_attempts = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM autopilot_work_units "
                    "WHERE job_id = ? AND phase = ?",
                    (job_id, str(row["phase"])),
                ).fetchone()[0]
            )
            discovery_units = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM autopilot_work_units "
                    "WHERE job_id = ? AND phase IN ('discover', 'targeted-discovery')",
                    (job_id,),
                ).fetchone()[0]
            )
            targeted_discovery_units = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM autopilot_work_units "
                    "WHERE job_id = ? AND phase = 'targeted-discovery'",
                    (job_id,),
                ).fetchone()[0]
            )
            receipt_rows = self._connection.execute(
                """
                SELECT operation, target, status, content_version,
                       after_sha256, start_line, end_line, result_count,
                       evidence_fingerprint, verified_progress
                FROM autopilot_tool_receipts WHERE job_id = ?
                """,
                (job_id,),
            ).fetchall()
        intervals: dict[str, list[tuple[int, int]]] = {}
        for receipt in receipt_rows:
            if (
                str(receipt["operation"]) == "read_file"
                and str(receipt["status"]) == "success"
                and receipt["start_line"] is not None
                and receipt["end_line"] is not None
            ):
                intervals.setdefault(str(receipt["target"]), []).append(
                    (int(receipt["start_line"]), int(receipt["end_line"]) + 1)
                )
        unique_lines = sum(_covered_units(values) for values in intervals.values())
        now = time.time()
        active_seconds = float(row["active_time_seconds"] or 0)
        if active is not None and active["started_at"] is not None:
            active_seconds += max(0.0, now - float(active["started_at"]))
        active_limit = float(row["active_time_limit_seconds"] or 0)
        wall_limit = float(row["wall_time_limit_seconds"] or 0)
        wall_elapsed = max(0.0, now - float(row["created_at"]))
        wall_deadline = float(row["wall_deadline_at"] or 0)
        unit_started = (
            float(active["started_at"])
            if active is not None and active["started_at"] is not None
            else 0.0
        )
        unit_deadline = (
            float(active["deadline_at"])
            if active is not None and active["deadline_at"] is not None
            else 0.0
        )
        try:
            parsed_next = json.loads(str(row["next_operation_json"] or "{}"))
        except (json.JSONDecodeError, TypeError):
            parsed_next = {}
        next_operation_data = parsed_next if isinstance(parsed_next, dict) else {}
        try:
            parsed_blocker = json.loads(str(row["blocker_json"] or "{}"))
        except (json.JSONDecodeError, TypeError):
            parsed_blocker = {}
        blocker = parsed_blocker if isinstance(parsed_blocker, dict) else {}
        changed_files = len(
            {
                str(item["target"])
                for item in receipt_rows
                if str(item["operation"])
                in {"write_file", "edit_file", "make_directory", "remove_path"}
                and str(item["status"]) == "success"
            }
        )
        return AutopilotProgress(
            job_id=str(row["id"]),
            status=str(row["status"]),
            phase=str(row["phase"]),
            mode=str(row["mode"]),
            audit_run_id=(str(row["audit_run_id"]) if row["audit_run_id"] else None),
            batch_size=int(row["batch_size"]),
            attempts=int(row["attempts"]),
            replans=int(row["replans"]),
            completed_units=counts.get("complete", 0),
            failed_units=counts.get("failed", 0),
            verification_status=str(row["verification_status"]),
            last_error_code=(
                str(row["last_error_code"]) if row["last_error_code"] else None
            ),
            requested_status=(
                str(row["control_requested"]) if row["control_requested"] else None
            ),
            interrupted_units=counts.get("interrupted", 0),
            lease_generation=int(row["lease_generation"] or 0),
            last_heartbeat_at=(
                float(row["last_heartbeat_at"])
                if row["last_heartbeat_at"] is not None
                else None
            ),
            active_unit_started_at=(
                float(active["started_at"])
                if active is not None and active["started_at"] is not None
                else None
            ),
            workflow=str(row["workflow"] or "project-audit"),
            yielded_units=counts.get("yielded", 0),
            file_reads=len(
                {
                    str(item["evidence_fingerprint"])
                    or "|".join(
                        str(item[name] or "")
                        for name in (
                            "operation",
                            "target",
                            "content_version",
                            "start_line",
                            "end_line",
                        )
                    )
                    for item in receipt_rows
                    if str(item["operation"]) == "read_file"
                    and str(item["status"]) == "success"
                }
            ),
            unique_lines_read=unique_lines,
            changed_files=changed_files,
            checks_run=len(
                {
                    str(item["evidence_fingerprint"])
                    or "|".join(
                        str(item[name] or "")
                        for name in ("operation", "target", "result_count")
                    )
                    for item in receipt_rows
                    if str(item["operation"]) == "run_project_checks"
                    and str(item["status"]) == "success"
                }
            ),
            escalation_count=int(row["escalation_count"] or 0),
            next_operation=str(row["next_operation"] or ""),
            next_operation_data=next_operation_data or None,
            phase_attempts=phase_attempts,
            checkpoint_revision=int(row["checkpoint_revision"] or 0),
            model_turn_timeout_seconds=float(row["model_turn_timeout_seconds"] or 0),
            unit_timeout_seconds=float(row["unit_timeout_seconds"] or 0),
            unit_time_seconds=max(0.0, now - unit_started) if unit_started else 0.0,
            unit_time_remaining_seconds=(
                max(0.0, unit_deadline - now) if unit_deadline else 0.0
            ),
            active_time_seconds=active_seconds,
            active_time_limit_seconds=active_limit,
            active_time_remaining_seconds=max(0.0, active_limit - active_seconds),
            wall_time_seconds=wall_elapsed,
            wall_time_limit_seconds=wall_limit,
            wall_time_remaining_seconds=(
                max(0.0, wall_deadline - now) if wall_deadline else 0.0
            ),
            discovery_units=discovery_units,
            targeted_discovery_units=targeted_discovery_units,
            discovery_searches=len(
                {
                    str(item["evidence_fingerprint"])
                    or "|".join(
                        str(item[name] or "")
                        for name in ("operation", "target", "result_count")
                    )
                    for item in receipt_rows
                    if str(item["operation"])
                    in {"glob", "grep", "search_context", "list_context_sources"}
                    and str(item["status"]) == "success"
                }
            ),
            last_progress_kind=str(row["last_progress_kind"] or ""),
            last_progress_at=(
                float(row["last_progress_at"])
                if row["last_progress_at"] is not None
                else None
            ),
            renewal_reason=str(row["renewal_reason"] or ""),
            blocker=blocker or None,
        )

    def details(self, job_id: str, *, unit_limit: int = 100) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM autopilot_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown autopilot job: {job_id}")
            units = [
                dict(item)
                for item in self._connection.execute(
                    """
                    SELECT id, sequence, phase, status, batch_size,
                           worker_thread_id, lease_generation, error_code, summary,
                           created_at, started_at, last_heartbeat_at, deadline_at,
                           finished_at
                    FROM autopilot_work_units WHERE job_id = ?
                    ORDER BY sequence DESC LIMIT ?
                    """,
                    (job_id, max(1, min(unit_limit, 500))),
                ).fetchall()
            ]
            receipts = [
                dict(item)
                for item in self._connection.execute(
                    """
                    SELECT id, unit_id, ordinal, operation, target, status,
                           content_version, before_sha256, after_sha256,
                           requested_offset, requested_limit, start_line, end_line,
                           next_cursor, bytes_returned, result_count,
                           evidence_fingerprint, verified_progress, created_at
                    FROM autopilot_tool_receipts WHERE job_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (job_id, max(1, min(unit_limit * 20, 2_000))),
                ).fetchall()
            ]
            transitions = [
                dict(item)
                for item in self._connection.execute(
                    "SELECT from_phase, to_phase, reason, created_at "
                    "FROM autopilot_phase_transitions WHERE job_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (job_id, max(1, min(unit_limit * 5, 500))),
                ).fetchall()
            ]
        details = dict(row)
        details.pop("lease_token", None)
        details.pop("lease_until", None)
        try:
            details["verification_results"] = json.loads(
                str(details.get("verification_results") or "[]")
            )
        except json.JSONDecodeError:
            details["verification_results"] = []
        for name in ("include_patterns", "exclude_patterns"):
            try:
                details[name] = json.loads(str(details.get(name) or "[]"))
            except json.JSONDecodeError:
                details[name] = []
        for name in ("next_operation_json", "blocker_json"):
            try:
                details[name] = json.loads(str(details.get(name) or "{}"))
            except json.JSONDecodeError:
                details[name] = {}
        details["progress"] = self.progress(job_id).as_dict()
        details["work_units"] = units
        details["tool_receipts"] = receipts
        details["phase_transitions"] = transitions
        return details

    def verified_mutation_count(self, job_id: str) -> int:
        """Return successful mutation receipts, including repeated edits per file."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) FROM autopilot_tool_receipts
                WHERE job_id = ?
                  AND operation IN (
                      'write_file', 'edit_file', 'make_directory', 'remove_path'
                  )
                  AND status = 'success'
                """,
                (job_id,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def recent_mutation_targets(self, job_id: str, *, limit: int = 100) -> set[str]:
        """Return virtual targets that already have successful mutation receipts."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT target FROM autopilot_tool_receipts
                WHERE job_id = ?
                  AND operation IN (
                      'write_file', 'edit_file', 'make_directory', 'remove_path'
                  )
                  AND status = 'success' AND target <> ''
                ORDER BY created_at DESC LIMIT ?
                """,
                (job_id, max(1, min(limit, 1_000))),
            ).fetchall()
        return {str(row["target"]) for row in rows}

    def recent_read_targets(self, job_id: str, *, limit: int = 20) -> list[str]:
        """Return bounded, newest-first targets backed by successful reads."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT target FROM autopilot_tool_receipts
                WHERE job_id = ? AND operation = 'read_file'
                  AND status = 'success' AND target != ''
                GROUP BY target ORDER BY MAX(created_at) DESC LIMIT ?
                """,
                (job_id, max(1, min(limit, 100))),
            ).fetchall()
        return [str(row["target"]) for row in rows]

    def list_jobs(self, *, workspace: Path, limit: int = 50) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, thread_id, objective_sha256, mode, workflow,
                       status, phase,
                       batch_size, attempts, replans, verification_status,
                       last_error_code, lease_generation, last_heartbeat_at,
                       checkpoint_revision,
                       created_at, updated_at, finished_at
                FROM autopilot_jobs WHERE workspace = ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (str(workspace.resolve()), max(1, min(limit, 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def _finish_unit(
        self,
        lease: AutopilotLease,
        unit_id: str,
        status: str,
        error_code: str | None,
        summary: str,
    ) -> None:
        with self._lock, self._connection:
            self._require_lease(lease)
            unit = self._connection.execute(
                "SELECT started_at FROM autopilot_work_units WHERE id = ? "
                "AND job_id = ? AND lease_generation = ? AND status = 'running'",
                (unit_id, lease.job_id, lease.generation),
            ).fetchone()
            if unit is None:
                raise AutopilotLeaseError("Autopilot work unit is not active")
            verified_delta = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM autopilot_tool_receipts "
                    "WHERE unit_id = ? AND verified_progress = 1",
                    (unit_id,),
                ).fetchone()[0]
            )
            finished_at = time.time()
            monotonic_started = self._unit_monotonic_starts.pop(unit_id, None)
            active_delta = (
                max(0.0, time.monotonic() - monotonic_started)
                if monotonic_started is not None
                else max(0.0, finished_at - float(unit["started_at"]))
                if unit["started_at"] is not None
                else 0.0
            )
            cursor = self._connection.execute(
                """
                UPDATE autopilot_work_units
                SET status = ?, error_code = ?, summary = ?, finished_at = ?
                WHERE id = ? AND job_id = ? AND lease_generation = ?
                    AND status = 'running'
                """,
                (
                    status,
                    error_code[:100] if error_code else None,
                    summary[:_SUMMARY_LIMIT],
                    finished_at,
                    unit_id,
                    lease.job_id,
                    lease.generation,
                ),
            )
            if cursor.rowcount == 0:
                raise AutopilotLeaseError("Autopilot work unit is not active")
            self._connection.execute(
                """
                UPDATE autopilot_jobs SET updated_at = ?,
                    active_time_seconds = active_time_seconds + ?,
                    renewal_reason = ?
                WHERE id = ? AND lease_token = ? AND lease_generation = ?
                """,
                (
                    finished_at,
                    active_delta,
                    (
                        "verified-handoff"
                        if status in {"complete", "yielded"} and verified_delta
                        else "no-verified-delta"
                        if status in {"complete", "yielded"}
                        else ""
                    ),
                    lease.job_id,
                    lease.token,
                    lease.generation,
                ),
            )

    def _leased_update(
        self,
        lease: AutopilotLease,
        assignments: str,
        values: tuple[object, ...],
    ) -> None:
        with self._lock, self._connection:
            self._require_lease(lease)
            cursor = self._connection.execute(
                f"UPDATE autopilot_jobs SET {assignments} "
                "WHERE id = ? AND lease_token = ? AND lease_generation = ?",
                (*values, lease.job_id, lease.token, lease.generation),
            )
            if cursor.rowcount == 0:
                raise AutopilotLeaseError("Autopilot job lease was lost")

    def _require_lease(self, lease: AutopilotLease) -> None:
        row = self._connection.execute(
            """
            SELECT lease_token, lease_until, lease_generation, status
            FROM autopilot_jobs WHERE id = ?
            """,
            (lease.job_id,),
        ).fetchone()
        if (
            row is None
            or not lease.token
            or str(row["lease_token"] or "") != lease.token
            or int(row["lease_generation"] or 0) != lease.generation
            or str(row["status"]) != "running"
            or float(row["lease_until"] or 0) <= time.time()
        ):
            raise AutopilotLeaseError("Autopilot job lease was lost")

    def _initialize_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 30000")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS autopilot_jobs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    objective_sha256 TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    workflow TEXT NOT NULL DEFAULT 'project-audit',
                    task_identity TEXT,
                    include_patterns TEXT NOT NULL DEFAULT '[]',
                    exclude_patterns TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    audit_run_id TEXT,
                    batch_size INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    replans INTEGER NOT NULL DEFAULT 0,
                    verification_status TEXT NOT NULL DEFAULT 'not_run',
                    verification_results TEXT NOT NULL DEFAULT '[]',
                    last_error_code TEXT,
                    last_error_message TEXT,
                    report TEXT NOT NULL DEFAULT '',
                    control_requested TEXT,
                    lease_token TEXT,
                    lease_until REAL,
                    lease_generation INTEGER NOT NULL DEFAULT 0,
                    last_heartbeat_at REAL,
                    checkpoint_revision INTEGER NOT NULL DEFAULT 0,
                    next_operation TEXT NOT NULL DEFAULT '',
                    next_operation_json TEXT NOT NULL DEFAULT '{}',
                    escalation_count INTEGER NOT NULL DEFAULT 0,
                    escalation_history TEXT NOT NULL DEFAULT '[]',
                    resume_phase TEXT NOT NULL DEFAULT '',
                    model_turn_timeout_seconds REAL NOT NULL DEFAULT 180,
                    unit_timeout_seconds REAL NOT NULL DEFAULT 900,
                    active_time_seconds REAL NOT NULL DEFAULT 0,
                    active_time_limit_seconds REAL NOT NULL DEFAULT 14400,
                    wall_time_limit_seconds REAL NOT NULL DEFAULT 86400,
                    wall_deadline_at REAL,
                    last_progress_kind TEXT NOT NULL DEFAULT '',
                    last_progress_at REAL,
                    renewal_reason TEXT NOT NULL DEFAULT '',
                    blocker_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_autopilot_jobs_workspace
                    ON autopilot_jobs(workspace, updated_at DESC);

                CREATE TABLE IF NOT EXISTS autopilot_work_units (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL
                        REFERENCES autopilot_jobs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    batch_size INTEGER NOT NULL,
                    worker_thread_id TEXT NOT NULL,
                    lease_generation INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    started_at REAL,
                    last_heartbeat_at REAL,
                    deadline_at REAL,
                    finished_at REAL,
                    UNIQUE(job_id, sequence)
                );

                CREATE INDEX IF NOT EXISTS idx_autopilot_units_job
                    ON autopilot_work_units(job_id, sequence);

                CREATE TABLE IF NOT EXISTS autopilot_tool_receipts (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL
                        REFERENCES autopilot_jobs(id) ON DELETE CASCADE,
                    unit_id TEXT NOT NULL
                        REFERENCES autopilot_work_units(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    content_version TEXT NOT NULL DEFAULT '',
                    before_sha256 TEXT NOT NULL DEFAULT '',
                    after_sha256 TEXT NOT NULL DEFAULT '',
                    requested_offset INTEGER,
                    requested_limit INTEGER,
                    start_line INTEGER,
                    end_line INTEGER,
                    next_cursor INTEGER,
                    bytes_returned INTEGER,
                    result_count INTEGER,
                    evidence_fingerprint TEXT NOT NULL DEFAULT '',
                    verified_progress INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    UNIQUE(unit_id, ordinal)
                );

                CREATE INDEX IF NOT EXISTS idx_autopilot_receipts_job
                    ON autopilot_tool_receipts(job_id, created_at);

                CREATE TABLE IF NOT EXISTS autopilot_phase_transitions (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL
                        REFERENCES autopilot_jobs(id) ON DELETE CASCADE,
                    from_phase TEXT NOT NULL,
                    to_phase TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_autopilot_phase_transitions_job
                    ON autopilot_phase_transitions(job_id, created_at);
                """
            )
            columns = {
                str(row["name"])
                for row in self._connection.execute(
                    "PRAGMA table_info(autopilot_jobs)"
                ).fetchall()
            }
            migrations = {
                "include_patterns": "TEXT NOT NULL DEFAULT '[]'",
                "exclude_patterns": "TEXT NOT NULL DEFAULT '[]'",
                "control_requested": "TEXT",
                "lease_generation": "INTEGER NOT NULL DEFAULT 0",
                "last_heartbeat_at": "REAL",
                "workflow": "TEXT NOT NULL DEFAULT 'project-audit'",
                "task_identity": "TEXT",
                "checkpoint_revision": "INTEGER NOT NULL DEFAULT 0",
                "next_operation": "TEXT NOT NULL DEFAULT ''",
                "next_operation_json": "TEXT NOT NULL DEFAULT '{}'",
                "escalation_count": "INTEGER NOT NULL DEFAULT 0",
                "escalation_history": "TEXT NOT NULL DEFAULT '[]'",
                "resume_phase": "TEXT NOT NULL DEFAULT ''",
                "model_turn_timeout_seconds": "REAL NOT NULL DEFAULT 180",
                "unit_timeout_seconds": "REAL NOT NULL DEFAULT 900",
                "active_time_seconds": "REAL NOT NULL DEFAULT 0",
                "active_time_limit_seconds": "REAL NOT NULL DEFAULT 14400",
                "wall_time_limit_seconds": "REAL NOT NULL DEFAULT 86400",
                "wall_deadline_at": "REAL",
                "last_progress_kind": "TEXT NOT NULL DEFAULT ''",
                "last_progress_at": "REAL",
                "renewal_reason": "TEXT NOT NULL DEFAULT ''",
                "blocker_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    self._connection.execute(
                        f"ALTER TABLE autopilot_jobs ADD COLUMN {name} {declaration}"
                    )
            unit_columns = {
                str(row["name"])
                for row in self._connection.execute(
                    "PRAGMA table_info(autopilot_work_units)"
                ).fetchall()
            }
            unit_migrations = {
                "lease_generation": "INTEGER NOT NULL DEFAULT 0",
                "last_heartbeat_at": "REAL",
                "deadline_at": "REAL",
            }
            for name, declaration in unit_migrations.items():
                if name not in unit_columns:
                    self._connection.execute(
                        "ALTER TABLE autopilot_work_units "
                        f"ADD COLUMN {name} {declaration}"
                    )
            receipt_columns = {
                str(row["name"])
                for row in self._connection.execute(
                    "PRAGMA table_info(autopilot_tool_receipts)"
                ).fetchall()
            }
            receipt_migrations = {
                "evidence_fingerprint": "TEXT NOT NULL DEFAULT ''",
                "verified_progress": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in receipt_migrations.items():
                if name not in receipt_columns:
                    self._connection.execute(
                        "ALTER TABLE autopilot_tool_receipts "
                        f"ADD COLUMN {name} {declaration}"
                    )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_autopilot_receipts_evidence "
                "ON autopilot_tool_receipts(job_id, evidence_fingerprint)"
            )
