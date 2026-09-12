"""Durable explicit task actions; reservations never grant execution authority."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from context_agent.paths import PathSecurityError, resolve_inside
from context_agent.routing import RoutingDecision
from context_agent.task_state import SavedTask, TaskConflict, TaskStateStore

_ACTIONS = {"continue", "continue_verification", "create_verification", "reconcile"}
_KEY = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_MUTATIONS = {"write_file", "edit_file", "delete_file", "delete_directory"}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)


def task_action_view(task: SavedTask) -> dict[str, Any]:
    """Cheap authoritative actions, not a model's paraphrase of old messages."""

    view = task.public()
    actions: list[str] = []
    reason = "TASK_TERMINAL"
    if task.status == "cancelled":
        reason = "TASK_CANCELLED"
    elif task.projection_state == "pending":
        reason = "FINALIZATION_PENDING"
    elif task.owner and task.lease_until >= time.time():
        reason = "TASK_BUSY"
    elif task.evidence.startswith("reconciliation_required:"):
        actions = ["reconcile", "create_verification"]
        reason = "EXECUTION_LINK_UNVERIFIED"
    elif task.status == "running":
        actions = ["reconcile"]
        reason = "RECONCILIATION_REQUIRED"
    elif task.status in {"partial", "blocked", "interrupted"}:
        actions = ["continue", "create_verification", "reconcile"]
        if task.routing.get("workflow") == "verification-only":
            actions.insert(1, "continue_verification")
        reason = "EXPLICIT_ACTION_AVAILABLE"
    elif task.status == "completed":
        actions = ["create_verification", "reconcile"]
    view.update(
        available_actions=actions,
        resumable="continue" in actions,
        reason_code=reason,
    )
    return view


@dataclass(frozen=True)
class ActionReservation:
    """Stable request receipt. Only its original creator may dispatch work."""

    id: str
    task_id: str
    thread: str
    action: str
    expected_revision: int
    execution_id: str
    linked_task_id: str | None
    project_root: str | None
    status: str
    result: dict[str, Any]
    created: bool = False

    def public(self) -> dict[str, Any]:
        values = asdict(self)
        del values["thread"]
        del values["created"]
        values["dispatch_confirmed"] = self.status == "dispatched"
        if self.status == "reserved":
            values["reason_code"] = "ACTION_DISPATCH_UNCONFIRMED"
        return values


class TaskActionStore:
    """Additive context-DB ledger with one dispatch per scoped task revision."""

    def __init__(
        self,
        database: Path,
        *,
        workspace: Path,
        autopilot_database: Path | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.autopilot_database = autopilot_database
        self.tasks = TaskStateStore(database)
        self.db = self.tasks.db
        with self.db:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS explicit_task_actions (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    thread TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    action TEXT NOT NULL,
                    expected_revision INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    execution_id TEXT NOT NULL UNIQUE,
                    linked_task_id TEXT,
                    project_root TEXT,
                    status TEXT NOT NULL,
                    result TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(task_id,thread,workspace,action,expected_revision)
                );
                CREATE TABLE IF NOT EXISTS linked_verification_tasks (
                    action_id TEXT PRIMARY KEY,
                    source_task_id TEXT NOT NULL,
                    source_revision INTEGER NOT NULL,
                    verification_task_id TEXT NOT NULL UNIQUE,
                    thread TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    project_root TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )

    def __enter__(self) -> TaskActionStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.db.close()

    @staticmethod
    def _decode(row: sqlite3.Row, *, created: bool = False) -> ActionReservation:
        return ActionReservation(
            **{
                name: row[name]
                for name in ActionReservation.__dataclass_fields__
                if name not in {"result", "created"}
            },
            result=json.loads(row["result"]),
            created=created,
        )

    def _source(self, task_id: str, thread: str) -> SavedTask:
        row = self.db.execute(
            "SELECT t.*, COALESCE((SELECT e.projection FROM task_terminal_events e "
            "WHERE e.task_id=t.id AND e.owner=t.owner AND e.revision=t.revision), "
            "'none') AS projection_state FROM authorized_tasks t "
            "WHERE t.id=? AND t.thread=? AND t.workspace=?",
            (task_id, thread, str(self.workspace)),
        ).fetchone()
        if row is None:
            raise TaskConflict("TASK_NOT_FOUND")
        return TaskStateStore._decode(row)

    def _project_root(self, value: str | None) -> str:
        if (
            not isinstance(value, str)
            or len(value) > 1_000
            or "\\" in value
            or any(ord(char) < 32 or char in "\"'" for char in value)
            or not (value == "/workspace" or value.startswith("/workspace/"))
            or any(part in {".", ".."} for part in value.split("/"))
        ):
            raise TaskConflict("EXPLICIT_PROJECT_ROOT_REQUIRED")
        try:
            root = resolve_inside(self.workspace, value, must_exist=True)
            manifest = resolve_inside(
                self.workspace, root / "pyproject.toml", must_exist=True
            )
            if not root.is_dir() or not manifest.is_file():
                raise TaskConflict("PROJECT_MANIFEST_REQUIRED")
        except (OSError, PathSecurityError) as exc:
            raise TaskConflict("PROJECT_ROOT_INVALID") from exc
        relative = root.relative_to(self.workspace).as_posix()
        return "/workspace" if relative == "." else f"/workspace/{relative}"

    @staticmethod
    def _validate_source(task: SavedTask, action: str, revision: int) -> None:
        if task.revision != revision:
            raise TaskConflict("STALE_TASK_REVISION")
        if action == "reconcile":
            return
        if task.status == "cancelled":
            raise TaskConflict("TASK_CANCELLED")
        if task.projection_state == "pending":
            raise TaskConflict("FINALIZATION_PENDING")
        if task.owner and task.lease_until >= time.time():
            raise TaskConflict("TASK_BUSY")
        if task.status == "running":
            raise TaskConflict("RECONCILIATION_REQUIRED")
        if action == "create_verification":
            return
        if task.status == "completed":
            raise TaskConflict("TASK_COMPLETED")
        if task.evidence.startswith("reconciliation_required:"):
            raise TaskConflict("EXECUTION_LINK_UNVERIFIED")
        if (
            action == "continue_verification"
            and task.routing.get("workflow") != "verification-only"
        ):
            raise TaskConflict("CREATE_LINKED_VERIFICATION_REQUIRED")

    def reserve(
        self,
        *,
        task_id: str,
        thread: str,
        action: str,
        expected_revision: int,
        idempotency_key: str,
        project_root: str | None = None,
        allow_write: bool = False,
    ) -> ActionReservation:
        """Seal an action before dispatch; retry never starts a second worker."""

        if action not in _ACTIONS:
            raise TaskConflict("INVALID_ACTION")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 1
            or not isinstance(idempotency_key, str)
            or _KEY.fullmatch(idempotency_key) is None
            or not isinstance(allow_write, bool)
        ):
            raise TaskConflict("INVALID_ACTION_REQUEST")
        if action != "create_verification" and project_root is not None:
            raise TaskConflict("UNEXPECTED_PROJECT_ROOT")
        # The fingerprint binds even a replay to its original scope and payload.
        fingerprint = hashlib.sha256(
            _canonical(
                [
                    task_id,
                    thread,
                    str(self.workspace),
                    action,
                    expected_revision,
                    project_root,
                    allow_write,
                ]
            ).encode()
        ).hexdigest()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute(
                "SELECT * FROM explicit_task_actions WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is None:
                existing = self.db.execute(
                    "SELECT * FROM explicit_task_actions WHERE task_id=? AND thread=? "
                    "AND workspace=? AND action=? AND expected_revision=?",
                    (task_id, thread, str(self.workspace), action, expected_revision),
                ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != fingerprint:
                    raise TaskConflict("IDEMPOTENCY_CONFLICT")
                self._source(task_id, thread)
                return self._decode(existing)
            source = self._source(task_id, thread)
            self._validate_source(source, action, expected_revision)
            root = (
                self._project_root(project_root)
                if action == "create_verification"
                else None
            )
            now = time.time()
            action_id = uuid4().hex
            self.db.execute(
                "INSERT INTO explicit_task_actions VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    action_id,
                    task_id,
                    thread,
                    str(self.workspace),
                    action,
                    expected_revision,
                    idempotency_key,
                    fingerprint,
                    uuid4().hex,
                    uuid4().hex if action == "create_verification" else None,
                    root,
                    "reserved",
                    "{}",
                    now,
                    now,
                ),
            )
            row = self.db.execute(
                "SELECT * FROM explicit_task_actions WHERE id=?", (action_id,)
            ).fetchone()
            assert row is not None
            return self._decode(row, created=True)

    def _reservation(self, item: ActionReservation) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM explicit_task_actions WHERE id=? AND workspace=? "
            "AND task_id=? AND thread=? AND execution_id=?",
            (
                item.id,
                str(self.workspace),
                item.task_id,
                item.thread,
                item.execution_id,
            ),
        ).fetchone()
        if row is None:
            raise TaskConflict("ACTION_NOT_FOUND")
        return row

    def get(self, *, action_id: str, task_id: str, thread: str) -> ActionReservation:
        """Read a scoped receipt without renewing it or authorizing redispatch."""

        self._source(task_id, thread)
        row = self.db.execute(
            "SELECT * FROM explicit_task_actions WHERE id=? AND task_id=? "
            "AND thread=? AND workspace=?",
            (action_id, task_id, thread, str(self.workspace)),
        ).fetchone()
        if row is None:
            raise TaskConflict("ACTION_NOT_FOUND")
        return self._decode(row)

    def mark_dispatched(
        self, reservation: ActionReservation, result: dict[str, Any]
    ) -> None:
        """Persist a bounded safe DTO after creation, never restart on retry."""

        encoded = _canonical(result)
        if len(encoded) > 16_000:
            raise TaskConflict("ACTION_RESULT_TOO_LARGE")
        with self.db:
            self._reservation(reservation)
            self.db.execute(
                "UPDATE explicit_task_actions SET status='dispatched',result=?, "
                "updated_at=? WHERE id=? AND status='reserved'",
                (encoded, time.time(), reservation.id),
            )

    def mark_failed(self, reservation: ActionReservation, reason_code: str) -> None:
        if re.fullmatch(r"[A-Z0-9_]{1,100}", reason_code) is None:
            reason_code = "ACTION_FAILED"
        with self.db:
            self._reservation(reservation)
            self.db.execute(
                "UPDATE explicit_task_actions SET status='failed',result=?, "
                "updated_at=? WHERE id=? AND status='reserved'",
                (_canonical({"reason_code": reason_code}), time.time(), reservation.id),
            )

    def create_linked_verification(self, item: ActionReservation) -> SavedTask:
        """Atomically create read-only VERIFY, leaving the source row unchanged."""

        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self._reservation(item)
            if row["action"] != "create_verification":
                raise TaskConflict("INVALID_ACTION")
            linked_id = str(row["linked_task_id"])
            existing = self.db.execute(
                "SELECT 1 FROM linked_verification_tasks WHERE action_id=?",
                (item.id,),
            ).fetchone()
            if existing:
                return self._source(linked_id, item.thread)
            if row["status"] != "reserved":
                raise TaskConflict("ACTION_NOT_PENDING")
            source = self._source(item.task_id, item.thread)
            self._validate_source(
                source, "create_verification", int(row["expected_revision"])
            )
            root = self._project_root(str(row["project_root"]))
            route = RoutingDecision(
                execution="persistent",
                workflow="verification-only",
                scope="project",
                allow_project_scan=False,
                allow_project_checks=True,
                confidence=1,
                reason_codes=("EXPLICIT_LINKED_VERIFICATION",),
                instruction_chars=0,
                excluded_data_chars=0,
                mutation_requested=False,
            )
            objective = (
                f'Run verification-only on project "{root}/pyproject.toml" '
                "using that project's "
                "Python environment. Run the complete configured verification plan "
                "on the current code. Do not modify source files, run a project "
                "audit, reuse an old PASS, or transition to implementation. "
                "Preserve concrete failed-check evidence for a separately approved "
                "repair task."
            )
            self.db.execute(
                "INSERT INTO authorized_tasks VALUES (?,?,?,?,?,?,?,1,NULL,0,?)",
                (
                    linked_id,
                    item.thread,
                    str(self.workspace),
                    objective,
                    _canonical(route.as_dict()),
                    0,
                    "partial",
                    "not_started",
                ),
            )
            # A provenance/root checkpoint is guidance, not inherited authority/PASS.
            checkpoint = {
                "source_task_id": source.id,
                "source_revision": source.revision,
                "explicit_project_root": root,
                "verification_status": "not_run",
                "workflow": "verification-only",
            }
            self.db.execute(
                "INSERT INTO task_checkpoints VALUES (?,?,?)",
                (linked_id, _canonical(checkpoint), time.time()),
            )
            self.db.execute(
                "INSERT INTO linked_verification_tasks VALUES (?,?,?,?,?,?,?,?)",
                (
                    item.id,
                    source.id,
                    source.revision,
                    linked_id,
                    item.thread,
                    str(self.workspace),
                    root,
                    time.time(),
                ),
            )
            return self._source(linked_id, item.thread)

    def reconcile(
        self, *, task_id: str, thread: str, expected_revision: int
    ) -> dict[str, Any]:
        """Inspect bounded evidence without clearing quarantine or replaying tools."""

        source = self._source(task_id, thread)
        self._validate_source(source, "reconcile", expected_revision)
        checkpoint_row = self.db.execute(
            "SELECT LENGTH(payload),SUBSTR(payload,1,64001) FROM task_checkpoints "
            "WHERE task_id=?",
            (source.id,),
        ).fetchone()
        checkpoint_valid = True
        if checkpoint_row:
            try:
                checkpoint_valid = int(checkpoint_row[0]) <= 64_000 and isinstance(
                    json.loads(checkpoint_row[1]), dict
                )
            except (TypeError, ValueError):
                checkpoint_valid = False
        links = self.db.execute(
            "SELECT * FROM task_execution_links WHERE task_id=? "
            "ORDER BY revision DESC LIMIT 2",
            (source.id,),
        ).fetchall()
        report: dict[str, Any] = {
            "task_id": source.id,
            "revision": source.revision,
            "status": source.public()["status"],
            "resumable": False,
            "reason_code": "EXECUTION_LINK_UNVERIFIED",
            "available_actions": ["create_verification"],
            "checkpoint_present": checkpoint_row is not None,
            "checkpoint_valid": checkpoint_valid,
            "execution_link_verified": False,
            "mutation_replay_allowed": False,
            "source_changed": False,
            "workspace_files_changed": False,
            "verification_status": "not_run",
            "receipt_count": 0,
            "file_evidence": "not_checked",
        }
        if source.status == "cancelled":
            report.update(reason_code="TASK_CANCELLED", available_actions=[])
        elif source.owner and source.lease_until >= time.time():
            report.update(reason_code="TASK_BUSY", available_actions=[])
        elif source.projection_state == "pending":
            report.update(reason_code="FINALIZATION_PENDING", available_actions=[])
        elif source.status == "running":
            report.update(
                reason_code="RECONCILIATION_REQUIRED", available_actions=["reconcile"]
            )
        elif not checkpoint_valid:
            report.update(reason_code="EVIDENCE_UNAVAILABLE")
        elif links and self.autopilot_database and self.autopilot_database.is_file():
            self._inspect_link(source, links[0], report)
        current = self._source(task_id, thread)
        if current != source:
            raise TaskConflict("STALE_TASK_REVISION")
        return report

    def _inspect_link(
        self, source: SavedTask, link: sqlite3.Row, report: dict[str, Any]
    ) -> None:
        assert self.autopilot_database is not None
        uri = self.autopilot_database.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN")
            job = connection.execute(
                "SELECT * FROM autopilot_jobs WHERE id=? AND workspace=? "
                "AND thread_id=? AND task_identity=? AND lease_generation=?",
                (
                    link["job_id"],
                    str(self.workspace),
                    source.thread,
                    source.id,
                    link["job_generation"],
                ),
            ).fetchone()
            if job is None:
                return
            if (job["lease_until"] or 0) >= time.time() and job["lease_token"]:
                report.update(reason_code="TASK_BUSY", available_actions=[])
                return
            report.update(
                execution_link_verified=True,
                job_id=job["id"],
                job_generation=job["lease_generation"],
                job_status=job["status"],
                verification_status=job["verification_status"],
                reason_code="LINK_VERIFIED_NEW_VERIFICATION_REQUIRED",
            )
            counts = connection.execute(
                "SELECT COUNT(*) FROM autopilot_tool_receipts WHERE job_id=?",
                (job["id"],),
            ).fetchone()
            report["receipt_count"] = int(counts[0])
            receipts = connection.execute(
                "SELECT operation,target,status,after_sha256 "
                "FROM autopilot_tool_receipts WHERE job_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT 33",
                (job["id"],),
            ).fetchall()
            report["receipt_inspection_partial"] = len(receipts) > 32
            report["file_evidence"] = self._inspect_files(receipts[:32])
            report["workspace_files_changed"] = report["file_evidence"] == "changed"
            # A historical link proves attribution, not a current safe replay point.
            # Preserve quarantine and offer fresh checks instead of lifting it.
        except (sqlite3.DatabaseError, OSError, ValueError):
            report.update(reason_code="EVIDENCE_UNAVAILABLE", resumable=False)
        finally:
            connection.close()

    def _inspect_files(self, receipts: list[sqlite3.Row]) -> str:
        seen: set[str] = set()
        verified = 0
        unknown = False
        for receipt in receipts:
            if receipt["operation"] not in _MUTATIONS:
                continue
            target = str(receipt["target"])
            if target in seen:
                continue
            seen.add(target)
            digest = str(receipt["after_sha256"])
            if receipt["status"] != "success" or not _DIGEST.fullmatch(digest):
                unknown = True
                continue
            try:
                path = resolve_inside(self.workspace, target, must_exist=True)
                if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                    unknown = True
                    continue
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, PathSecurityError):
                unknown = True
                continue
            if actual != digest:
                return "changed"
            verified += 1
        return "unknown" if unknown else "matched" if verified else "not_available"
