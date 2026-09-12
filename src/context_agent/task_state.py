"""Persist authorized objectives separately from conversational side questions."""

# ruff: noqa: RUF001 -- bilingual operator messages

from __future__ import annotations

import builtins
import json
import math
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from context_agent.diagnostics import redact_sensitive_text
from context_agent.intent import IntentDecision, SemanticClassifier, resolve_intent
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
    projection_state: str = "none"

    def public(self) -> dict[str, Any]:
        """Expose no physical path or objective in default status responses."""
        return {
            "task_id": self.id,
            "status": (
                "finalization_pending"
                if self.projection_state == "pending"
                else "reconciliation_required"
                if self.evidence.startswith("reconciliation_required:")
                else self.status
            ),
            "projection_state": self.projection_state,
            "revision": self.revision,
            "lease_remaining_seconds": (
                round(max(0, self.lease_until - time.time()), 3)
                if self.status == "running"
                else 0
            ),
            "workflow": self.routing["workflow"],
            "scope": self.routing["scope"],
            "allow_write": self.allow_write,
            "evidence": self.evidence,
        }


class TaskStateStore:
    """An additive table in context SQLite; atomic claims fence stale workers."""

    def __init__(self, database: Path, *, timeout_seconds: float = 10) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, timeout=timeout_seconds)
        self.db.row_factory = sqlite3.Row
        self.db.execute(f"PRAGMA busy_timeout={int(timeout_seconds * 1000)}")
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
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS task_terminal_events ("
            "id TEXT PRIMARY KEY, task_id TEXT NOT NULL, owner TEXT NOT NULL, "
            "revision INTEGER NOT NULL, status TEXT NOT NULL, evidence TEXT NOT NULL, "
            "created_at REAL NOT NULL, projection TEXT NOT NULL DEFAULT 'pending', "
            "UNIQUE(task_id, owner, revision))"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS task_terminal_pending "
            "ON task_terminal_events(projection, created_at)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS task_execution_links ("
            "task_id TEXT NOT NULL, owner TEXT NOT NULL, revision INTEGER NOT NULL, "
            "job_id TEXT NOT NULL, job_generation INTEGER NOT NULL, "
            "PRIMARY KEY(task_id,owner,revision))"
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
            "SELECT t.*, COALESCE((SELECT e.projection FROM task_terminal_events e "
            "WHERE e.task_id=t.id AND e.owner=t.owner AND e.revision=t.revision), "
            "'none') AS projection_state FROM authorized_tasks t "
            "WHERE thread=? AND workspace=? ORDER BY t.rowid DESC LIMIT 100",
            (thread, str(workspace.resolve())),
        ).fetchall()
        return [self._decode(row) for row in rows]

    def public_by_id(self, task_id: str, workspace: Path) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT t.*, COALESCE((SELECT e.projection FROM task_terminal_events e "
            "WHERE e.task_id=t.id AND e.owner=t.owner AND e.revision=t.revision), "
            "'none') AS projection_state FROM authorized_tasks t "
            "WHERE id=? AND workspace=?",
            (task_id, str(workspace.resolve())),
        ).fetchone()
        return self._decode(row).public() if row else {}

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

    def create_explicit(
        self,
        *,
        task_id: str,
        thread: str,
        workspace: Path,
        objective: str,
        route: RoutingDecision,
        allow_write: bool,
        evidence: str,
        known_secrets: tuple[str, ...] = (),
    ) -> SavedTask:
        """Create a trusted server-owned task without semantic intent routing."""

        if not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise TaskConflict("Explicit task identity is invalid")
        clean_objective = redact_sensitive_text(
            objective.strip(), known_secrets=known_secrets
        )
        if not clean_objective or len(clean_objective) > 16_000:
            raise TaskConflict("Explicit task objective is invalid")
        task = SavedTask(
            task_id,
            thread.strip(),
            str(workspace.resolve()),
            clean_objective,
            route.as_dict(),
            bool(allow_write),
            "partial",
            1,
            None,
            0,
            redact_sensitive_text(evidence, known_secrets=known_secrets)[:2_000],
        )
        values = asdict(task)
        values["routing"] = json.dumps(task.routing, ensure_ascii=False)
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO authorized_tasks VALUES "
                    "(:id,:thread,:workspace,:objective,:routing,:allow_write,"
                    ":status,:revision,:owner,:lease_until,:evidence)",
                    values,
                )
        except sqlite3.IntegrityError as exc:
            raise TaskConflict("Explicit task identity already exists") from exc
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
        explicit_action: str | None = None,
        expected_revision: int | None = None,
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
        if explicit_action is not None:
            if explicit_action not in {"continue", "continue_verification"}:
                raise TaskConflict("INVALID_ACTION")
            if task_id is None or expected_revision is None:
                raise TaskConflict("TASK_AND_REVISION_REQUIRED")
            selected = self.resolve(thread, workspace, task_id)
            if selected.revision != expected_revision:
                raise TaskConflict("STALE_TASK_REVISION")
            if selected.evidence.startswith("reconciliation_required:"):
                raise TaskConflict("EXECUTION_LINK_UNVERIFIED: reconciliation required")
            if (
                explicit_action == "continue_verification"
                and selected.routing["workflow"] != "verification-only"
            ):
                raise TaskConflict("CREATE_LINKED_VERIFICATION_REQUIRED")
            if explicit_action == "continue_verification" and mode in {"ask", "plan"}:
                raise TaskConflict("ACTION_NOT_AVAILABLE_IN_MODE")
            intent = IntentDecision("resume_task", task_id, reason="EXPLICIT_ACTION")
        else:
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
            if explicit_action == "continue_verification":
                # A typed operator action authorizes fixed checks, never a broad
                # audit or code changes. Old derived routing flags are not policy.
                route = replace(
                    route,
                    workflow="verification-only",
                    execution="persistent",
                    allow_project_checks=True,
                    allow_project_scan=False,
                    mutation_requested=False,
                    reason_codes=(*route.reason_codes, "EXPLICIT_VERIFY_ONLY"),
                )
        elif intent.action == "side_question":
            route = replace(
                route,
                mutation_requested=False,
                allow_project_scan=False,
                allow_project_checks=False,
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
        if task.evidence.startswith("reconciliation_required:"):
            raise TaskConflict(
                "Reconciliation required: inspect receipts and create an explicit "
                "new task; automatic replay is disabled."
            )
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
            if str(row["evidence"]).startswith("reconciliation_required:"):
                raise TaskConflict("Legacy execution requires explicit reconciliation")
            if row["owner"] is not None and float(row["lease_until"]) >= now:
                raise TaskConflict(
                    "Saved-task lease is still live; recovery cannot take it"
                )
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
                "AND revision=? AND lease_until>=? AND status='running' "
                "AND NOT EXISTS (SELECT 1 FROM task_terminal_events e WHERE "
                "e.task_id=authorized_tasks.id AND e.owner=authorized_tasks.owner "
                "AND e.revision=authorized_tasks.revision)",
                (task.id, task.owner, task.revision, time.time()),
            ).fetchone()
            is not None
        )

    def renew(self, task: SavedTask, seconds: float) -> bool:
        """Extend only a live owner without changing its fencing revision."""
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Lease duration must be finite and positive")
        now = time.time()
        with self.db:
            return (
                self.db.execute(
                    "UPDATE authorized_tasks SET lease_until=MAX(lease_until, ?) "
                    "WHERE id=? AND owner=? AND revision=? AND status='running' "
                    "AND lease_until>=? AND NOT EXISTS (SELECT 1 FROM "
                    "task_terminal_events e WHERE e.task_id=authorized_tasks.id "
                    "AND e.owner=authorized_tasks.owner "
                    "AND e.revision=authorized_tasks.revision)",
                    (now + seconds, task.id, task.owner, task.revision, now),
                ).rowcount
                == 1
            )

    def authority_reason(self, task: SavedTask) -> str | None:
        """Return a bounded public reason, never the current owner's identity."""
        row = self.db.execute(
            "SELECT owner, revision, status, lease_until FROM authorized_tasks "
            "WHERE id=?",
            (task.id,),
        ).fetchone()
        if row is None or row["status"] != "running":
            return "revoked"
        if row["owner"] != task.owner or row["revision"] != task.revision:
            return "replaced"
        if row["lease_until"] < time.time():
            return "expired"
        return None if self.owns(task) else "revoked"

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

    def record_terminal(self, task: SavedTask, status: str, evidence: str) -> str:
        """Controller-only outcome record; never grants expired worker authority."""
        if status not in {*_RESUMABLE, "completed", "cancelled"}:
            raise ValueError("Invalid task status")
        if not task.owner:
            raise TaskConflict("Terminal outcome requires execution identity")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute(
                "SELECT id, status FROM task_terminal_events "
                "WHERE task_id=? AND owner=? AND revision=?",
                (task.id, task.owner, task.revision),
            ).fetchone()
            if existing:
                if existing["status"] != status:
                    raise TaskConflict("Execution already has another terminal outcome")
                return str(existing["id"])
            current = self.db.execute(
                "SELECT 1 FROM authorized_tasks WHERE id=? AND owner=? "
                "AND revision=? AND status='running'",
                (task.id, task.owner, task.revision),
            ).fetchone()
            if current is None:
                raise TaskConflict("Terminal execution has been superseded")
            event_id = uuid4().hex
            self.db.execute(
                "INSERT INTO task_terminal_events "
                "(id,task_id,owner,revision,status,evidence,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    event_id,
                    task.id,
                    task.owner,
                    task.revision,
                    status,
                    redact_sensitive_text(evidence)[:2000],
                    time.time(),
                ),
            )
        return event_id

    def bind_execution(self, task: SavedTask, job_id: str, generation: int) -> None:
        """Persist the exact job generation before the worker executes tools."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if not self.owns(task):
                raise TaskConflict("Cannot bind a job to stale task ownership")
            self.db.execute(
                "INSERT INTO task_execution_links VALUES (?,?,?,?,?) "
                "ON CONFLICT(task_id,owner,revision) DO UPDATE SET "
                "job_id=excluded.job_id, job_generation=excluded.job_generation",
                (task.id, task.owner, task.revision, job_id, generation),
            )

    def linked_running(
        self, *, limit: int = 100
    ) -> builtins.list[tuple[SavedTask, str, int]]:
        """Return only generation-matched links, never infer identity from prose."""
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid reconciliation limit")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS task_reconcile_cursor "
                "(id INTEGER PRIMARY KEY CHECK(id=1), after_id TEXT NOT NULL)"
            )
            cursor = self.db.execute(
                "SELECT after_id FROM task_reconcile_cursor WHERE id=1"
            ).fetchone()
            after_id = str(cursor[0]) if cursor else ""
            query = (
                "SELECT t.*, l.job_id, l.job_generation FROM authorized_tasks t "
                "JOIN task_execution_links l ON l.task_id=t.id AND l.owner=t.owner "
                "AND l.revision=t.revision WHERE t.status='running' "
                "AND t.id>? ORDER BY t.id LIMIT ?"
            )
            rows = self.db.execute(query, (after_id, limit)).fetchall()
            if not rows and after_id:
                rows = self.db.execute(query, ("", limit)).fetchall()
            self.db.execute(
                "INSERT INTO task_reconcile_cursor VALUES (1,?) "
                "ON CONFLICT(id) DO UPDATE SET after_id=excluded.after_id",
                (str(rows[-1]["id"]) if rows else "",),
            )
        result = []
        for row in rows:
            values = dict(row)
            job_id = values.pop("job_id")
            generation = values.pop("job_generation")
            values["routing"] = json.loads(values["routing"])
            values["allow_write"] = bool(values["allow_write"])
            result.append((SavedTask(**values), str(job_id), int(generation)))
        return result

    def reconcile_unlinked_expired(self, *, limit: int = 100) -> int:
        """Quarantine orphaned executions; never invent a task/job association."""
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid reconciliation limit")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return self.db.execute(
                "UPDATE authorized_tasks SET status='blocked', owner=NULL, "
                "lease_until=0, revision=revision+1, evidence=? WHERE id IN "
                "(SELECT t.id FROM authorized_tasks t WHERE t.status='running' "
                "AND t.lease_until<? AND NOT EXISTS "
                "(SELECT 1 FROM task_execution_links l WHERE l.task_id=t.id "
                "AND l.owner=t.owner AND l.revision=t.revision) "
                "ORDER BY t.id LIMIT ?)",
                (
                    "reconciliation_required: no verified execution link; inspect "
                    "receipts/files before creating an explicitly scoped new task",
                    time.time(),
                    limit,
                ),
            ).rowcount

    def project_terminal(self, event_id: str) -> str:
        """Apply durable controller evidence with fencing, even after lease expiry."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            event = self.db.execute(
                "SELECT * FROM task_terminal_events WHERE id=?", (event_id,)
            ).fetchone()
            if event is None:
                raise TaskConflict("Terminal event is unavailable")
            if event["projection"] != "pending":
                return str(event["projection"])
            changed = self.db.execute(
                "UPDATE authorized_tasks SET status=?, evidence=?, owner=NULL, "
                "lease_until=0, revision=revision+1 WHERE id=? AND owner=? "
                "AND revision=? AND status='running'",
                (
                    event["status"],
                    event["evidence"],
                    event["task_id"],
                    event["owner"],
                    event["revision"],
                ),
            ).rowcount
            projection = "projected" if changed == 1 else "superseded"
            self.db.execute(
                "UPDATE task_terminal_events SET projection=? WHERE id=?",
                (projection, event_id),
            )
        return projection

    def finalize(self, task: SavedTask, status: str, evidence: str) -> str:
        """Persist first, then project; a crash between commits is recoverable."""
        try:
            event_id = self.record_terminal(task, status, evidence)
        except TaskConflict:
            current = self.db.execute(
                "SELECT 1 FROM authorized_tasks WHERE id=? AND owner=? "
                "AND revision=? AND status='running'",
                (task.id, task.owner, task.revision),
            ).fetchone()
            if current is None:
                return "superseded"
            raise
        return self.project_terminal(event_id)

    def reconcile_terminals(self, *, limit: int = 100) -> int:
        """Replay a bounded page without starting jobs or altering other owners."""
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid reconciliation limit")
        events = self.db.execute(
            "SELECT id FROM task_terminal_events WHERE projection='pending' "
            "ORDER BY created_at, id LIMIT ?",
            (limit,),
        ).fetchall()
        for event in events:
            self.project_terminal(str(event["id"]))
        return len(events)


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
        allow_project_checks=bool(
            saved.get(
                "allow_project_checks",
                saved.get("workflow")
                in {"project-change", "project-test", "verification-only"},
            )
        ),
        mutation_requested=(
            task.allow_write
            and mode not in {"ask", "plan"}
            and "READ_ONLY_INTENT" not in current.reason_codes
        ),
        reason_codes=(*current.reason_codes, "SAVED_TASK_CONTINUATION"),
    )
