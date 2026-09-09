"""Independent saved-task heartbeat; model progress is not lease authority."""

from __future__ import annotations

import math
import sqlite3
import threading
from pathlib import Path

from context_agent.task_state import SavedTask, TaskConflict, TaskStateStore


class TaskLeaseHeartbeat:
    """Use thread-local SQLite connections and fail closed on renewal failure."""

    def __init__(
        self,
        database: Path,
        task: SavedTask,
        *,
        seconds: float,
        interval: float,
    ) -> None:
        if not (math.isfinite(seconds) and math.isfinite(interval)):
            raise ValueError("Lease duration and interval must be finite")
        if seconds <= 0 or not 0 < interval <= seconds / 3:
            raise ValueError("Invalid lease duration or heartbeat interval")
        self.database = database
        self.task = task
        self.seconds = seconds
        self.interval = interval
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"task-lease-{task.id[:8]}", daemon=True
        )

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        if not self._renew():
            raise TaskConflict("Saved-task lease is no longer owned")
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=2)

    def _renew(self) -> bool:
        try:
            with TaskStateStore(self.database, timeout_seconds=0.25) as store:
                renewed = store.renew(self.task, self.seconds)
        except (sqlite3.Error, OSError):
            renewed = False
        if not renewed:
            self._lost.set()
        return renewed

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            if not self._renew():
                return


def reconcile_execution_states(database: Path, jobs_database: Path) -> int:
    """Recover task projections from fenced job evidence, without restarting jobs."""
    with TaskStateStore(database, timeout_seconds=0.25) as state:
        count = state.reconcile_terminals()
        if not jobs_database.is_file():
            return count
        jobs = sqlite3.connect(
            jobs_database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25
        )
        jobs.row_factory = sqlite3.Row
        try:
            for task, job_id, generation in state.linked_running():
                job = jobs.execute(
                    "SELECT status, task_identity, lease_generation, workspace, "
                    "last_error_code FROM autopilot_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                if (
                    job is None
                    or job["task_identity"] != task.id
                    or job["lease_generation"] != generation
                    or job["workspace"] != task.workspace
                ):
                    continue
                status = {
                    "blocked": "blocked",
                    "cancelled": "cancelled",
                    "complete": "completed"
                    if task.routing["workflow"] == "verification-only"
                    else "partial",
                    "partial": "partial",
                    "paused": "interrupted",
                }.get(job["status"])
                if status is not None:
                    state.finalize(
                        task,
                        status,
                        f"job:{job_id}; outcome:{status}; "
                        f"error:{job['last_error_code'] or 'none'}",
                    )
                    count += 1
        finally:
            jobs.close()
        return count
