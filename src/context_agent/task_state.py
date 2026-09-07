"""Persist authorized objectives separately from conversational side questions."""

# ruff: noqa: RUF001 -- bilingual operator messages

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from context_agent.diagnostics import redact_sensitive_text
from context_agent.intent import SemanticClassifier, resolve_intent
from context_agent.routing import (
    RoutingDecision,
    Workflow,
    extract_direct_instruction,
    route_chat_request,
)

_RESUMABLE = ("partial", "blocked", "interrupted")


class TaskConflict(ValueError):  # noqa: N818 -- API conflict signal
    """A continuation cannot safely identify or claim one task."""


@dataclass(frozen=True)
class SavedTask:
    """Authority originates only from a direct user request and trusted controls."""

    id: str
    thread: str
    workspace: str
    objective: str
    routing: dict[str, Any]
    allow_write: bool
    status: str
    revision: int
    owner: str | None
    lease_until: float
    evidence: str

    def public(self) -> dict[str, Any]:
        """Expose no physical path or objective in default status responses."""
        return {
            "task_id": self.id,
            "status": self.status,
            "revision": self.revision,
            "workflow": self.routing["workflow"],
            "scope": self.routing["scope"],
            "allow_write": self.allow_write,
            "evidence": self.evidence,
        }


class TaskStateStore:
    """An additive table in context SQLite; atomic claims fence stale workers."""

    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS authorized_tasks ("
            "id TEXT PRIMARY KEY, thread TEXT NOT NULL, workspace TEXT NOT NULL,"
            "objective TEXT NOT NULL, routing TEXT NOT NULL, allow_write INTEGER "
            "NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL, owner TEXT,"
            "lease_until REAL NOT NULL, evidence TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS authorized_tasks_thread "
            "ON authorized_tasks(thread, workspace, status)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS task_checkpoints ("
            "task_id TEXT PRIMARY KEY REFERENCES authorized_tasks(id), "
            "payload TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        self.db.commit()

    def __enter__(self) -> TaskStateStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.db.close()

    @staticmethod
    def _decode(row: sqlite3.Row) -> SavedTask:
        values = dict(row)
        values["routing"] = json.loads(values["routing"])
        values["allow_write"] = bool(values["allow_write"])
        return SavedTask(**values)

    def list(self, thread: str, workspace: Path) -> list[SavedTask]:
        rows = self.db.execute(
            "SELECT * FROM authorized_tasks WHERE thread=? AND workspace=? "
            "ORDER BY rowid DESC LIMIT 100",
            (thread, str(workspace.resolve())),
        ).fetchall()
        return [self._decode(row) for row in rows]

    def create(
        self,
        thread: str,
        workspace: Path,
        query: str,
        route: RoutingDecision,
        allow_write: bool,
        *,
        known_secrets: tuple[str, ...] = (),
    ) -> SavedTask:
        objective = extract_direct_instruction(query).text
        if len(objective) > 16_000:
            raise TaskConflict("Task objective exceeds 16000 characters; use a file.")
        task = SavedTask(
            uuid4().hex,
            thread,
            str(workspace.resolve()),
            redact_sensitive_text(objective, known_secrets=known_secrets),
            route.as_dict(),
            allow_write,
            "partial",
            1,
            None,
            0,
            "not_started",
        )
        values = asdict(task)
        values["routing"] = json.dumps(task.routing)
        with self.db:
            self.db.execute(
                "INSERT INTO authorized_tasks VALUES "
                "(:id,:thread,:workspace,:objective,:routing,:allow_write,"
                ":status,:revision,:owner,:lease_until,:evidence)",
                values,
            )
        return task

    def resolve(
        self,
        thread: str,
        workspace: Path,
        task_id: str | None = None,
    ) -> SavedTask:
        if task_id is not None:
            row = self.db.execute(
                "SELECT * FROM authorized_tasks "
                "WHERE id=? AND thread=? AND workspace=?",
                (task_id, thread, str(workspace.resolve())),
            ).fetchone()
            tasks = [self._decode(row)] if row else []
        else:
            # Never guess the sole candidate by examining only the first page.
            rows = self.db.execute(
                "SELECT * FROM authorized_tasks WHERE thread=? AND workspace=? "
                "AND status IN ('partial','blocked','interrupted','running') LIMIT 2",
                (thread, str(workspace.resolve())),
            ).fetchall()
            tasks = [self._decode(row) for row in rows]
        candidates = [
            task
            for task in tasks
            if (task.status in _RESUMABLE or task.status == "running")
            and (task_id is None or task.id == task_id)
        ]
        if len(candidates) != 1:
            raise TaskConflict(
                "Select one unfinished task / Выберите одну незавершённую задачу."
            )
        return candidates[0]

    def prepare_turn(
        self,
        *,
        query: str,
        thread: str,
        workspace: Path,
        mode: str,
        execution: Any,
        allow_write: bool,
        owner: str,
        lease_seconds: float,
        task_id: str | None = None,
        known_secrets: tuple[str, ...] = (),
        semantic: SemanticClassifier | None = None,
    ) -> tuple[SavedTask | None, RoutingDecision]:
        """Shared Web/CLI authority resolution; quoted data never selects a task."""
        route = route_chat_request(query, work_mode=mode, requested_execution=execution)
        task = None
        created = False
        candidates = [
            item.id
            for item in self.list(thread, workspace)
            if item.status in {*_RESUMABLE, "running"}
        ]
        intent = resolve_intent(
            query, candidates, selected_task_id=task_id, semantic=semantic
        )
        if intent.action == "clarify":
            raise TaskConflict(
                "Не удалось однозначно определить продолжение. Выберите задачу "
                f"и уточните следующий шаг ({intent.reason})."
            )
        if intent.action == "resume_task":
            task = self.resolve(thread, workspace, intent.task_id)
            route = resume_route(task, query, mode, execution)
        elif intent.action == "side_question":
            route = replace(
                route,
                mutation_requested=False,
                allow_project_scan=False,
                execution="persistent" if execution == "autopilot" else "single-turn",
                workflow="log-analysis"
                if route.workflow == "log-analysis"
                else "answer",
            )
        elif (
            intent.action == "new_task"
            and route.scope in {"file", "project"}
            and route.workflow != "log-analysis"
            and (route.mutation_requested or route.execution == "persistent")
            and mode not in {"ask", "plan"}
        ):
            task = self.create(
                thread,
                workspace,
                query,
                route,
                allow_write
                and route.mutation_requested
                and mode not in {"ask", "plan"},
                known_secrets=known_secrets,
            )
            created = True
        route = replace(
            route,
            intent={**intent.as_dict(), "task_id": task.id if task else None},
            reason_codes=(
                *route.reason_codes,
                f"INTENT_{intent.action.upper()}",
                intent.reason,
            ),
        )
        if task is not None:
            try:
                task = self.claim(task, owner, lease_seconds)
            except TaskConflict:
                if created:
                    with self.db:
                        self.db.execute(
                            "DELETE FROM authorized_tasks WHERE id=? "
                            "AND owner IS NULL AND revision=1",
                            (task.id,),
                        )
                raise
        return task, route

    def checkpoint(self, task: SavedTask) -> dict[str, Any]:
        return self.checkpoint_by_id(task.id)

    def checkpoint_by_id(self, task_id: str) -> dict[str, Any]:
        """Read a checkpoint by durable identity during worker recovery."""

        row = self.db.execute(
            "SELECT payload FROM task_checkpoints WHERE task_id=?", (task_id,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def save_checkpoint(
        self,
        task: SavedTask,
        payload: dict[str, Any],
        *,
        known_secrets: tuple[str, ...] = (),
    ) -> None:
        """Fence checkpoint writes exactly like workspace side effects."""
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 64_000:
            raise ValueError("Task checkpoint exceeds 64000 bytes")

        # Redact string leaves, not serialized JSON (redaction can break JSON).
        def redact(value: Any) -> Any:
            if isinstance(value, str):
                return redact_sensitive_text(value, known_secrets=known_secrets)
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            return value

        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if not self.owns(task):
                raise TaskConflict("Task authority lost; checkpoint was not saved")
            self.db.execute(
                "INSERT INTO task_checkpoints VALUES (?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (task.id, json.dumps(redact(payload), ensure_ascii=False), time.time()),
            )

    def claim(self, task: SavedTask, owner: str, seconds: float) -> SavedTask:
        now = time.time()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            busy = self.db.execute(
                "SELECT 1 FROM authorized_tasks WHERE thread=? AND workspace=? "
                "AND owner IS NOT NULL AND lease_until>=? LIMIT 1",
                (task.thread, task.workspace, now),
            ).fetchone()
            if busy:
                raise TaskConflict("Task is busy / В этом чате уже выполняется задача.")
            changed = self.db.execute(
                "UPDATE authorized_tasks SET owner=?, lease_until=?, status='running',"
                "revision=revision+1 WHERE id=? AND revision=? "
                "AND status NOT IN ('completed','cancelled') "
                "AND (owner IS NULL OR lease_until < ?)",
                (owner, now + seconds, task.id, task.revision, now),
            ).rowcount
            if changed != 1:
                raise TaskConflict(
                    "Task is busy or stale / Задача занята или изменена."
                )
        return replace(
            task,
            owner=owner,
            lease_until=now + seconds,
            revision=task.revision + 1,
            status="running",
        )

    def recover_claim(
        self,
        task_id: str,
        thread: str,
        workspace: Path,
        owner: str,
        seconds: float,
    ) -> SavedTask:
        """Fence an owner after its linked Autopilot lease was reconciled."""

        now = time.time()
        resolved_workspace = str(workspace.resolve())
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT * FROM authorized_tasks WHERE id=? AND thread=? "
                "AND workspace=? AND status NOT IN ('completed','cancelled')",
                (task_id, thread, resolved_workspace),
            ).fetchone()
            if row is None:
                raise TaskConflict("Saved task is unavailable for restart recovery")
            revision = int(row["revision"]) + 1
            changed = self.db.execute(
                "UPDATE authorized_tasks SET owner=?, lease_until=?, "
                "status='running', revision=? WHERE id=? AND revision=?",
                (owner, now + seconds, revision, task_id, int(row["revision"])),
            ).rowcount
            if changed != 1:
                raise TaskConflict("Saved task changed during restart recovery")
        return replace(
            self._decode(row),
            owner=owner,
            lease_until=now + seconds,
            revision=revision,
            status="running",
        )

    def owns(self, task: SavedTask) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM authorized_tasks WHERE id=? AND owner=? "
                "AND revision=? AND lease_until>=? AND status='running'",
                (task.id, task.owner, task.revision, time.time()),
            ).fetchone()
            is not None
        )

    def cancel_owner(self, owner: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE authorized_tasks SET status='cancelled', owner=NULL, "
                "revision=revision+1, lease_until=0, evidence='operator_cancelled' "
                "WHERE owner=? AND status='running'",
                (owner,),
            )

    def close_task(
        self,
        task_id: str,
        thread: str,
        workspace: Path,
        revision: int,
        status: str,
    ) -> str | None:
        if status not in {"cancelled", "completed"}:
            raise ValueError("Invalid terminal status")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            previous = self.db.execute(
                "SELECT owner FROM authorized_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            changed = self.db.execute(
                "UPDATE authorized_tasks SET status=?, owner=NULL, lease_until=0, "
                "revision=revision+1, evidence=? WHERE id=? AND thread=? "
                "AND workspace=? AND revision=? AND (owner IS NULL OR ?='cancelled')",
                (
                    status,
                    f"operator_marked_{status}",
                    task_id,
                    thread,
                    str(workspace.resolve()),
                    revision,
                    status,
                ),
            ).rowcount
            if changed != 1:
                raise TaskConflict("Task changed or is running; refresh task status.")
        return str(previous["owner"]) if previous and previous["owner"] else None

    def finish(self, task: SavedTask, status: str, evidence: str) -> bool:
        if status not in {*_RESUMABLE, "completed", "cancelled"}:
            raise ValueError("Invalid task status")
        with self.db:
            return (
                self.db.execute(
                    "UPDATE authorized_tasks SET status=?, evidence=?, owner=NULL, "
                    "lease_until=0, revision=revision+1 WHERE id=? "
                    "AND owner=? AND revision=? AND lease_until>=? "
                    "AND status='running'",
                    (
                        status,
                        evidence[:2000],
                        task.id,
                        task.owner,
                        task.revision,
                        time.time(),
                    ),
                ).rowcount
                == 1
            )


def is_continuation(query: str) -> bool:
    """Recognize only the complete direct command, never quoted instructions."""
    return resolve_intent(query).action == "resume_task"


def resume_route(
    task: SavedTask,
    query: str,
    mode: str,
    execution: Any,
) -> RoutingDecision:
    """Recover scope, while preserving current mode and execution restrictions."""
    current = route_chat_request(query, work_mode=mode, requested_execution=execution)
    saved = task.routing
    single = mode in {"ask", "plan", "debug"} or execution == "single-turn"
    return replace(
        current,
        execution="single-turn" if single else saved["execution"],
        workflow=cast(
            Workflow, mode if mode in {"plan", "debug"} else saved["workflow"]
        ),
        scope=saved["scope"],
        allow_project_scan=bool(saved["allow_project_scan"]),
        mutation_requested=(
            task.allow_write
            and mode not in {"ask", "plan"}
            and "READ_ONLY_INTENT" not in current.reason_codes
        ),
        reason_codes=(*current.reason_codes, "SAVED_TASK_CONTINUATION"),
    )
