"""Persistent orchestration state for long-running project jobs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"blocked", "cancelled", "complete"}
)
_CONTROL_STATUSES: Final[frozenset[str]] = frozenset({"running", "paused", "cancelled"})
_REPORT_LIMIT: Final[int] = 100_000
_SUMMARY_LIMIT: Final[int] = 4_000


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
                    SET status = 'paused', phase = 'interrupted',
                        last_error_code = 'autopilot_lease_expired',
                        last_error_message =
                            'Controller lease expired; resume is safe.',
                        lease_token = NULL, lease_until = NULL,
                        updated_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, job_id),
                )
        return len(job_ids)

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
    ) -> tuple[AutopilotProgress, AutopilotLease]:
        """Create/resume a stable identity and claim an exclusive lease."""

        if not thread_id.strip():
            raise ValueError("thread_id cannot be empty")
        if not objective.strip():
            raise ValueError("Autopilot objective cannot be empty")
        if not 1 <= batch_size <= 25:
            raise ValueError("Autopilot batch size must be between 1 and 25")
        mode = "allow-write" if allow_write else "read-only"
        resolved_workspace = workspace.resolve()
        job_id = self.job_id_for(
            thread_id=thread_id,
            objective=objective,
            workspace=resolved_workspace,
            allow_write=allow_write,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )
        token = uuid4().hex
        now = time.time()
        lease_until = now + lease_seconds
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT status, lease_token, lease_until, lease_generation "
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
                self._connection.execute(
                    """
                    INSERT INTO autopilot_jobs(
                        id, thread_id, objective, objective_sha256, workspace, mode,
                        include_patterns, exclude_patterns,
                        status, phase, audit_run_id, batch_size, attempts, replans,
                        verification_status, verification_results, last_error_code,
                        last_error_message, report, control_requested,
                        lease_token, lease_until, lease_generation, last_heartbeat_at,
                        created_at, updated_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', 'audit', NULL, ?, 0, 0,
                              'not_run', '[]', NULL, NULL, '', NULL, ?, ?, ?, ?,
                              ?, ?, NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        status = 'running',
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
                        json.dumps(include_patterns, ensure_ascii=False),
                        json.dumps(exclude_patterns, ensure_ascii=False),
                        batch_size,
                        token,
                        lease_until,
                        generation,
                        now,
                        now,
                        now,
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
    ) -> str:
        """Return the stable identity shared by CLI, Web, and the store."""

        mode = "allow-write" if allow_write else "read-only"
        identity = "\0".join(
            (
                "deep-context-autopilot-v1",
                str(workspace.resolve()).casefold(),
                thread_id.strip(),
                objective.strip(),
                mode,
                json.dumps(include_patterns, ensure_ascii=False),
                json.dumps(exclude_patterns, ensure_ascii=False),
            )
        )
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
            lease_token = NULL, lease_until = NULL,
            updated_at = ?, finished_at = ?
            """,
            (report[:_REPORT_LIMIT], now, now),
        )
        return self.progress(lease.job_id)

    def mark_blocked(
        self,
        lease: AutopilotLease,
        *,
        error_code: str,
        safe_message: str,
        report: str = "",
    ) -> AutopilotProgress:
        now = time.time()
        self._leased_update(
            lease,
            """
            status = 'blocked', phase = 'blocked', last_error_code = ?,
            last_error_message = ?, report = ?, lease_token = NULL,
            lease_until = NULL, updated_at = ?, finished_at = ?
            """,
            (
                error_code[:100],
                safe_message[:_SUMMARY_LIMIT],
                report[:_REPORT_LIMIT],
                now,
                now,
            ),
        )
        return self.progress(lease.job_id)

    def set_control_status(self, job_id: str, status: str) -> AutopilotProgress:
        if status not in _CONTROL_STATUSES:
            raise ValueError("Unsupported autopilot control status")
        now = time.time()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status, lease_token, lease_until FROM autopilot_jobs "
                "WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown autopilot job: {job_id}")
            if str(row["status"]) == "complete":
                return self.progress(job_id)
            active_lease = (
                bool(row["lease_token"]) and float(row["lease_until"] or 0) > now
            )
            if active_lease:
                self._connection.execute(
                    """
                    UPDATE autopilot_jobs
                    SET control_requested = ?, updated_at = ? WHERE id = ?
                    """,
                    (status, now, job_id),
                )
            else:
                finished_at = now if status == "cancelled" else None
                self._connection.execute(
                    """
                    UPDATE autopilot_jobs
                    SET status = ?, control_requested = NULL,
                        lease_token = NULL, lease_until = NULL,
                        updated_at = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (status, now, finished_at, job_id),
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
                SET status = ?, phase = ?, control_requested = NULL,
                    lease_token = NULL, lease_until = NULL,
                    updated_at = ?, finished_at = ?
                WHERE id = ? AND lease_token = ? AND lease_generation = ?
                """,
                (
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
                       control_requested, lease_generation, last_heartbeat_at
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
                SELECT started_at FROM autopilot_work_units
                WHERE job_id = ? AND status = 'running'
                ORDER BY sequence DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
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
        details["progress"] = self.progress(job_id).as_dict()
        details["work_units"] = units
        return details

    def list_jobs(self, *, workspace: Path, limit: int = 50) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, thread_id, objective_sha256, mode, status, phase,
                       batch_size, attempts, replans, verification_status,
                       last_error_code, lease_generation, last_heartbeat_at,
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
                    time.time(),
                    unit_id,
                    lease.job_id,
                    lease.generation,
                ),
            )
            if cursor.rowcount == 0:
                raise AutopilotLeaseError("Autopilot work unit is not active")
            self._connection.execute(
                """
                UPDATE autopilot_jobs SET updated_at = ?
                WHERE id = ? AND lease_token = ? AND lease_generation = ?
                """,
                (time.time(), lease.job_id, lease.token, lease.generation),
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
