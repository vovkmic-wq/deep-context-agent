"""Scoped explicit-action reservations, recovery inspection and fresh VERIFY."""

import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from context_agent.autopilot import AutopilotStore
from context_agent.routing import route_chat_request
from context_agent.task_actions import TaskActionStore, task_action_view
from context_agent.task_state import TaskConflict, TaskStateStore


def source_task(database, root, *, readonly=False):
    with TaskStateStore(database) as state:
        return state.create(
            "test",
            root,
            "Fix project code",
            route_chat_request("Fix project code"),
            not readonly,
        )


def reserve(store, task, **changes):
    options = dict(
        task_id=task.id,
        thread="test",
        action="continue",
        expected_revision=task.revision,
        idempotency_key="action-key-123",
    )
    options.update(changes)
    return store.reserve(**options)


def test_reservation_survives_restart_and_double_click(tmp_path):
    database = tmp_path / "context.sqlite3"
    task = source_task(database, tmp_path)
    with TaskActionStore(database, workspace=tmp_path) as store:
        first = reserve(store, task)
        assert first.created
        second = reserve(store, task, idempotency_key="second-click-123")
        assert not second.created
        assert second.id == first.id
        assert second.execution_id == first.execution_id
        store.mark_dispatched(first, {"task_id": first.execution_id})
    with TaskStateStore(database) as state:
        claimed = state.claim(task, "owner", 30)
        state.finish(claimed, "completed", "verified")
    with TaskActionStore(database, workspace=tmp_path) as restarted:
        repeated = reserve(restarted, task)
        assert not repeated.created
        assert repeated.status == "dispatched"
        assert repeated.result == {"task_id": first.execution_id}
        assert "workspace" not in repeated.public()


def test_concurrent_reservation_has_one_dispatch_owner(tmp_path):
    database = tmp_path / "context.sqlite3"
    task = source_task(database, tmp_path)
    # Initialize schema outside concurrent calls, just as app startup does.
    with TaskActionStore(database, workspace=tmp_path):
        pass

    def click(index):
        with TaskActionStore(database, workspace=tmp_path) as store:
            return reserve(store, task, idempotency_key=f"click-{index:08}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        replies = list(pool.map(click, range(6)))
    assert sum(reply.created for reply in replies) == 1
    assert len({reply.execution_id for reply in replies}) == 1


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"thread": "other"}, "TASK_NOT_FOUND"),
        ({"expected_revision": 99}, "STALE_TASK_REVISION"),
        ({"expected_revision": True}, "INVALID_ACTION_REQUEST"),
        ({"action": "override"}, "INVALID_ACTION"),
        ({"idempotency_key": ""}, "INVALID_ACTION_REQUEST"),
        ({"project_root": "/workspace"}, "UNEXPECTED_PROJECT_ROOT"),
        ({"action": "continue_verification"}, "CREATE_LINKED_VERIFICATION_REQUIRED"),
    ],
)
def test_bad_actions_rejected(tmp_path, change, reason):
    database = tmp_path / "context.sqlite3"
    task = source_task(database, tmp_path)
    with (
        TaskActionStore(database, workspace=tmp_path) as store,
        pytest.raises(TaskConflict, match=reason),
    ):
        reserve(store, task, **change)


def test_idempotency_key_cannot_change_permission_or_scope(tmp_path):
    database = tmp_path / "context.sqlite3"
    task = source_task(database, tmp_path)
    other_root = tmp_path / "other"
    other_root.mkdir()
    with TaskActionStore(database, workspace=tmp_path) as store:
        reserve(store, task)
        for change in ({"allow_write": True}, {"thread": "other"}):
            with pytest.raises(TaskConflict, match="IDEMPOTENCY_CONFLICT"):
                reserve(store, task, **change)
    with (
        TaskActionStore(database, workspace=other_root) as store,
        pytest.raises(TaskConflict, match="IDEMPOTENCY_CONFLICT"),
    ):
        reserve(store, task)


def test_failed_or_crashed_reservation_cannot_automatically_replay(tmp_path):
    database = tmp_path / "context.sqlite3"
    task = source_task(database, tmp_path)
    with TaskActionStore(database, workspace=tmp_path) as store:
        pending = reserve(store, task)
    with TaskActionStore(database, workspace=tmp_path) as restarted:
        repeated = reserve(restarted, task)
        assert repeated.status == "reserved"
        assert not repeated.created
        snapshot = restarted.get(action_id=pending.id, task_id=task.id, thread="test")
        assert snapshot.public()["reason_code"] == "ACTION_DISPATCH_UNCONFIRMED"
        assert not snapshot.public()["dispatch_confirmed"]
        assert not snapshot.created
        with pytest.raises(TaskConflict, match="TASK_NOT_FOUND"):
            restarted.get(action_id=pending.id, task_id=task.id, thread="other")
        restarted.mark_failed(pending, "WORKER_START_FAILED")
        failed = reserve(restarted, task)
        assert failed.status == "failed"
        assert failed.result["reason_code"] == "WORKER_START_FAILED"
        assert not failed.created


@pytest.mark.parametrize(
    "payload",
    ["{broken", "[]", '"text"', "x" * 64_001],
    ids=["invalid-json", "array", "string", "oversized"],
)
def test_corrupt_checkpoint_is_bounded_reconciliation_evidence(tmp_path, payload):
    database = tmp_path / "context.sqlite3"
    task = source_task(database, tmp_path)
    with TaskActionStore(database, workspace=tmp_path) as store:
        with store.db:
            store.db.execute(
                "INSERT INTO task_checkpoints VALUES (?,?,?)",
                (task.id, payload, time.time()),
            )
        report = store.reconcile(
            task_id=task.id,
            thread="test",
            expected_revision=task.revision,
        )
        assert report["reason_code"] == "EVIDENCE_UNAVAILABLE"
        assert not report["checkpoint_valid"]
        assert not report["resumable"]
        assert "payload" not in report


def test_expired_running_task_reports_no_new_verification_action(tmp_path):
    database = tmp_path / "context.sqlite3"
    (tmp_path / "pyproject.toml").write_text("[project]\nname='example'\n")
    task = source_task(database, tmp_path)
    with TaskStateStore(database) as state:
        task = state.claim(task, "old-worker", 30)
        with state.db:
            state.db.execute(
                "UPDATE authorized_tasks SET lease_until=? WHERE id=?",
                (time.time() - 30, task.id),
            )
    with TaskActionStore(database, workspace=tmp_path) as store:
        report = store.reconcile(
            task_id=task.id,
            thread="test",
            expected_revision=task.revision,
        )
        assert report["reason_code"] == "RECONCILIATION_REQUIRED"
        assert report["available_actions"] == ["reconcile"]
        with pytest.raises(TaskConflict, match="RECONCILIATION_REQUIRED"):
            reserve(
                store,
                task,
                action="create_verification",
                project_root="/workspace",
            )


def test_live_lease_pending_terminal_cancelled_and_quarantine(tmp_path):
    database = tmp_path / "context.sqlite3"
    task = source_task(database, tmp_path)
    with TaskStateStore(database) as state:
        claimed = state.claim(task, "worker", 30)
    with (
        TaskActionStore(database, workspace=tmp_path) as store,
        pytest.raises(TaskConflict, match="TASK_BUSY"),
    ):
        reserve(store, claimed)
    with TaskStateStore(database) as state:
        event = state.record_terminal(claimed, "blocked", "failure")
    with (
        TaskActionStore(database, workspace=tmp_path) as store,
        pytest.raises(TaskConflict, match="FINALIZATION_PENDING"),
    ):
        reserve(store, claimed)
    with TaskStateStore(database) as state:
        state.project_terminal(event)
        blocked = state.resolve("test", tmp_path, task.id)
        claimed = state.claim(blocked, "worker2", 30)
        state.finish(claimed, "blocked", "reconciliation_required: unknown mutation")
        orphan = state.resolve("test", tmp_path, task.id)
    with (
        TaskActionStore(database, workspace=tmp_path) as store,
        pytest.raises(TaskConflict, match="EXECUTION_LINK_UNVERIFIED"),
    ):
        reserve(store, orphan)
    with TaskStateStore(database) as state:
        state.close_task(orphan.id, "test", tmp_path, orphan.revision, "cancelled")
    with (
        TaskActionStore(database, workspace=tmp_path) as store,
        pytest.raises(TaskConflict, match="TASK_CANCELLED"),
    ):
        reserve(store, orphan, expected_revision=orphan.revision + 1)


def test_linked_verification_is_atomic_readonly_and_source_unchanged(tmp_path):
    database = tmp_path / "context.sqlite3"
    project = tmp_path / "nested project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='example'\n")
    task = source_task(database, tmp_path)
    with TaskStateStore(database) as state:
        owned = state.claim(task, "worker", 30)
        state.save_checkpoint(owned, {"verification_status": "passed", "private": 1})
        state.finish(owned, "blocked", "reconciliation_required: unknown result")
        task = state.resolve("test", tmp_path, task.id)
        before = tuple(state.db.execute("SELECT * FROM authorized_tasks").fetchone())
        checkpoint_before = state.checkpoint_by_id(task.id)
    with TaskActionStore(database, workspace=tmp_path) as store:
        action = reserve(
            store,
            task,
            action="create_verification",
            allow_write=True,
            project_root="/workspace/nested project",
        )
        linked = store.create_linked_verification(action)
        assert not linked.allow_write
        assert linked.routing["workflow"] == "verification-only"
        assert not linked.routing["allow_project_scan"]
        assert linked.routing["allow_project_checks"]
        assert '"/workspace/nested project/pyproject.toml"' in linked.objective
        assert store.create_linked_verification(action).id == linked.id
        assert (
            store.tasks.checkpoint_by_id(linked.id)["verification_status"] == "not_run"
        )
        assert (
            tuple(
                store.db.execute(
                    "SELECT * FROM authorized_tasks WHERE id=?",
                    (task.id,),
                ).fetchone()
            )
            == before
        )
        assert store.tasks.checkpoint_by_id(task.id) == checkpoint_before
    with TaskActionStore(database, workspace=tmp_path) as restarted:
        replay = reserve(
            restarted,
            task,
            action="create_verification",
            allow_write=True,
            project_root="/workspace/nested project",
        )
        assert not replay.created
        assert restarted.create_linked_verification(replay).id == linked.id


@pytest.mark.parametrize(
    "root",
    [
        None,
        "C:/outside",
        "/workspace/../outside",
        "/workspace/missing",
        "/workspace/fake",
        "/workspace/evil\nrun",
        "/workspace/quoted'root",
    ],
)
def test_explicit_project_root_is_required_and_validated(tmp_path, root):
    database = tmp_path / "context.sqlite3"
    (tmp_path / "fake").mkdir()
    task = source_task(database, tmp_path)
    with TaskActionStore(database, workspace=tmp_path) as store:
        with pytest.raises(TaskConflict):
            reserve(store, task, action="create_verification", project_root=root)
        assert (
            store.db.execute(
                "SELECT COUNT(*) FROM linked_verification_tasks"
            ).fetchone()[0]
            == 0
        )


def test_creation_rechecks_source_revision_after_reservation(tmp_path):
    database = tmp_path / "context.sqlite3"
    (tmp_path / "pyproject.toml").write_text("[project]\nname='example'\n")
    task = source_task(database, tmp_path)
    with TaskActionStore(database, workspace=tmp_path) as store:
        reserved = reserve(
            store,
            task,
            action="create_verification",
            project_root="/workspace",
        )
    with TaskStateStore(database) as state:
        state.close_task(task.id, "test", tmp_path, task.revision, "cancelled")
    with TaskActionStore(database, workspace=tmp_path) as store:
        with pytest.raises(TaskConflict, match="STALE_TASK_REVISION"):
            store.create_linked_verification(reserved)
        assert (
            store.db.execute("SELECT COUNT(*) FROM authorized_tasks").fetchone()[0] == 1
        )


def test_reconciliation_without_link_is_readonly_and_scoped(tmp_path):
    database = tmp_path / "context.sqlite3"
    task = source_task(database, tmp_path)
    with TaskStateStore(database) as state:
        claim = state.claim(task, "worker", 30)
        state.finish(claim, "blocked", "reconciliation_required: missing link")
        task = state.resolve("test", tmp_path, task.id)
    with TaskActionStore(database, workspace=tmp_path) as store:
        before = tuple(store.db.execute("SELECT * FROM authorized_tasks").fetchone())
        report = store.reconcile(
            task_id=task.id, thread="test", expected_revision=task.revision
        )
        assert report["reason_code"] == "EXECUTION_LINK_UNVERIFIED"
        assert not report["resumable"]
        assert not report["mutation_replay_allowed"]
        assert report["available_actions"] == ["create_verification"]
        assert (
            tuple(store.db.execute("SELECT * FROM authorized_tasks").fetchone())
            == before
        )
        with pytest.raises(TaskConflict, match="TASK_NOT_FOUND"):
            store.reconcile(
                task_id=task.id, thread="other", expected_revision=task.revision
            )


def test_link_inspection_checks_current_files_but_never_replays(tmp_path):
    database = tmp_path / "context.sqlite3"
    autopilot_db = tmp_path / "autopilot.sqlite3"
    target = tmp_path / "module.py"
    target.write_text("old_content = 1\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    task = source_task(database, tmp_path)
    with AutopilotStore(autopilot_db) as autopilot:
        job_id = autopilot.enqueue(
            thread_id="test",
            workspace=tmp_path,
            objective="Fix project code",
            allow_write=True,
            batch_size=2,
            workflow="project-change",
            task_identity=task.id,
        )
    with TaskStateStore(database) as state:
        claimed = state.claim(task, "worker", 30)
        state.bind_execution(claimed, job_id, 0)
        state.finish(
            claimed, "blocked", "reconciliation_required: unknown finalization"
        )
        task = state.resolve("test", tmp_path, task.id)
    with sqlite3.connect(autopilot_db) as db:
        db.execute(
            "INSERT INTO autopilot_tool_receipts (id,job_id,unit_id,ordinal,operation,"
            "target,status,after_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                job_id,
                "unit",
                1,
                "edit_file",
                "/workspace/module.py",
                "success",
                digest,
                time.time(),
            ),
        )
    with TaskActionStore(
        database, workspace=tmp_path, autopilot_database=autopilot_db
    ) as store:
        report = store.reconcile(
            task_id=task.id, thread="test", expected_revision=task.revision
        )
        assert report["execution_link_verified"]
        assert report["file_evidence"] == "matched"
        assert report["receipt_count"] == 1
        assert not report["resumable"]
        assert not report["mutation_replay_allowed"]
        target.write_text("new_content = 2\n")
        changed = store.reconcile(
            task_id=task.id, thread="test", expected_revision=task.revision
        )
        assert not changed["source_changed"]
        assert changed["workspace_files_changed"]
        assert changed["file_evidence"] == "changed"
        assert "old_content" not in json.dumps(changed)
    with sqlite3.connect(autopilot_db) as db:
        db.execute("UPDATE autopilot_jobs SET lease_generation=2 WHERE id=?", (job_id,))
    with TaskActionStore(
        database, workspace=tmp_path, autopilot_database=autopilot_db
    ) as store:
        stale = store.reconcile(
            task_id=task.id, thread="test", expected_revision=task.revision
        )
        assert not stale["execution_link_verified"]


def test_public_view_does_not_promote_reconciliation_or_write_permission(tmp_path):
    task = source_task(tmp_path / "context.sqlite3", tmp_path, readonly=True)
    view = task_action_view(task)
    assert view["resumable"]
    assert not view["allow_write"]
    assert "objective" not in view and "workspace" not in view
    blocked = replace(
        task, evidence="reconciliation_required: link missing", status="blocked"
    )
    assert not task_action_view(blocked)["resumable"]
    assert "continue" not in task_action_view(blocked)["available_actions"]
    running = replace(
        task, owner="live", lease_until=time.time() + 30, status="running"
    )
    assert task_action_view(running)["reason_code"] == "TASK_BUSY"
