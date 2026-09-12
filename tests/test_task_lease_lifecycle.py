"""Saved-task renewal must preserve fencing and reject expired workers."""

import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from context_agent.routing import route_chat_request
from context_agent.task_lifecycle import TaskLeaseHeartbeat, reconcile_execution_states
from context_agent.task_state import TaskConflict, TaskStateStore


def claimed(store, root):
    query = "Implement the project feature"
    task = store.create("main", root, query, route_chat_request(query), True)
    return store.claim(task, "worker-a", 10)


def test_busy_retry_recovers_without_changing_generation(tmp_path):
    database = tmp_path / "busy.sqlite3"
    with TaskStateStore(database) as state:
        task = claimed(state, tmp_path)
    locked = threading.Event()

    def lock_briefly():
        with sqlite3.connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            locked.set()
            threading.Event().wait(0.2)

    thread = threading.Thread(target=lock_briefly)
    thread.start()
    assert locked.wait(2)
    heartbeat = TaskLeaseHeartbeat(database, task, seconds=10, interval=1)
    try:
        heartbeat.start()
        assert not heartbeat.lost
        assert heartbeat.reason is None
        with TaskStateStore(database) as state:
            assert state.list("main", tmp_path)[0].revision == task.revision
    finally:
        heartbeat.close()
        thread.join(2)


def test_busy_retry_is_bounded_and_fails_closed(tmp_path):
    database = tmp_path / "busy.sqlite3"
    with TaskStateStore(database) as state:
        task = claimed(state, tmp_path)
    heartbeat = TaskLeaseHeartbeat(database, task, seconds=10, interval=1)
    with sqlite3.connect(database) as locked:
        locked.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        assert not heartbeat._renew()
        assert time.monotonic() - started < 1.5
    assert heartbeat.lost
    assert heartbeat.reason == "storage_unavailable"


def test_authority_reasons_are_distinct(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("context_agent.task_state.time.time", lambda: now[0])
    with TaskStateStore(tmp_path / "state.sqlite3") as state:
        task = claimed(state, tmp_path)
        assert state.authority_reason(task) is None
        now[0] = 111
        assert state.authority_reason(task) == "expired"
        replacement = state.claim(task, "other-worker", 10)
        assert state.authority_reason(task) == "replaced"
        state.cancel_owner(replacement.owner)
        assert state.authority_reason(replacement) == "revoked"


def test_orphan_migration_is_explicit_and_does_not_capture_live_owner(
    tmp_path, monkeypatch
):
    now = [100.0]
    monkeypatch.setattr("context_agent.task_state.time.time", lambda: now[0])
    database = tmp_path / "legacy.sqlite3"
    with TaskStateStore(database) as state:
        task = claimed(state, tmp_path)
        assert state.reconcile_unlinked_expired() == 0
        now[0] = 111
        assert state.reconcile_unlinked_expired() == 1
    with TaskStateStore(database) as state:
        assert state.reconcile_unlinked_expired() == 0
        final = state.list("main", tmp_path)[0]
        assert final.public()["status"] == "reconciliation_required"
        assert final.owner is None
        with pytest.raises(TaskConflict, match="Reconciliation"):
            state.claim(final, "replacement", 10)
        assert not state.renew(task, 10)


def test_renew_preserves_owner_revision_and_authority(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("context_agent.task_state.time.time", lambda: now[0])
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        task = claimed(store, tmp_path)
        now[0] = 109
        assert store.renew(task, 10)
        now[0] = 115
        assert store.owns(task)
        current = store.list("main", tmp_path)[0]
        assert current.revision == task.revision
        assert current.owner == task.owner
        assert current.allow_write == task.allow_write
        assert current.lease_until == 119
        assert store.finish(task, "blocked", "verification_failed")


def test_expired_owner_cannot_renew_or_displace_replacement(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("context_agent.task_state.time.time", lambda: now[0])
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        task = claimed(store, tmp_path)
        now[0] = 111
        assert not store.renew(task, 10)
        replacement = store.claim(task, "worker-b", 10)
        assert not store.renew(task, 10)
        assert not store.finish(task, "completed", "late reply")
        assert store.owns(replacement)


def test_cancelled_owner_cannot_renew(tmp_path):
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        task = claimed(store, tmp_path)
        store.cancel_owner(task.owner)
        assert not store.renew(task, 10)
        assert store.list("main", tmp_path)[0].status == "cancelled"


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_invalid_renewal_duration_rejected(tmp_path, seconds):
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        task = claimed(store, tmp_path)
        with pytest.raises(ValueError, match="duration"):
            store.renew(task, seconds)


def test_heartbeat_renews_without_model_or_tool_activity(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("context_agent.task_state.time.time", lambda: now[0])
    database = tmp_path / "state.sqlite3"
    with TaskStateStore(database) as store:
        task = claimed(store, tmp_path)
    renewed = threading.Event()
    original = TaskStateStore.renew

    def notify(store, task, seconds):
        result = original(store, task, seconds)
        if now[0] == 109 and result:
            renewed.set()
        return result

    monkeypatch.setattr(TaskStateStore, "renew", notify)
    heartbeat = TaskLeaseHeartbeat(database, task, seconds=10, interval=0.01)
    heartbeat.start()
    try:
        now[0] = 109
        assert renewed.wait(2), "Independent heartbeat did not execute"
        now[0] = 115
        with TaskStateStore(database) as store:
            assert store.owns(task)
    finally:
        heartbeat.close()
    assert not heartbeat._thread.is_alive()


def test_terminal_outbox_survives_expiry_and_restart(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("context_agent.task_state.time.time", lambda: now[0])
    database = tmp_path / "state.sqlite3"
    with TaskStateStore(database) as store:
        task = claimed(store, tmp_path)
        now[0] = 111
        assert not store.finish(task, "blocked", "lease expired")
        event_id = store.record_terminal(task, "blocked", "job:fixture; failed")
        assert store.list("main", tmp_path)[0].status == "running"
        assert (
            store.list("main", tmp_path)[0].public()["status"] == "finalization_pending"
        )
        assert not store.owns(task)
    with TaskStateStore(database) as store:
        assert store.reconcile_terminals() == 1
        assert store.reconcile_terminals() == 0
        assert store.project_terminal(event_id) == "projected"
        final = store.list("main", tmp_path)[0]
        assert final.status == "blocked"
        assert final.owner is None
        assert final.revision == task.revision + 1
        assert not store.renew(task, 10)


def test_terminal_projection_cannot_overwrite_takeover(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("context_agent.task_state.time.time", lambda: now[0])
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        task = claimed(store, tmp_path)
        event_id = store.record_terminal(task, "blocked", "worker A failed")
        now[0] = 111
        replacement = store.claim(task, "worker-b", 10)
        assert store.project_terminal(event_id) == "superseded"
        assert store.owns(replacement)


def test_terminal_projection_respects_operator_cancel(tmp_path):
    with TaskStateStore(tmp_path / "state.sqlite3") as store:
        task = claimed(store, tmp_path)
        event_id = store.record_terminal(task, "completed", "checks passed")
        store.cancel_owner(task.owner)
        assert store.project_terminal(event_id) == "superseded"
        assert store.list("main", tmp_path)[0].status == "cancelled"


@pytest.mark.parametrize("generation, expected", [(7, "blocked"), (8, "running")])
def test_recovery_from_job_commit_is_generation_fenced(tmp_path, generation, expected):
    database = tmp_path / "state.sqlite3"
    jobs_database = tmp_path / "jobs.sqlite3"
    with TaskStateStore(database) as store:
        task = claimed(store, tmp_path)
        store.bind_execution(task, "fixture-job", 7)
    with sqlite3.connect(jobs_database) as jobs:
        jobs.execute(
            "CREATE TABLE autopilot_jobs (id TEXT, status TEXT, task_identity TEXT, "
            "lease_generation INTEGER, workspace TEXT, last_error_code TEXT)"
        )
        jobs.execute(
            "INSERT INTO autopilot_jobs VALUES (?, ?, ?, ?, ?, ?)",
            ("fixture-job", "blocked", task.id, generation, task.workspace, "timeout"),
        )
    reconcile_execution_states(database, jobs_database)
    with TaskStateStore(database) as store:
        current = store.list("main", tmp_path)[0]
        assert current.status == expected
        if expected == "blocked":
            assert "timeout" in current.evidence
            assert current.owner is None
    assert reconcile_execution_states(database, jobs_database) == 0


def test_real_silent_subprocess_outlives_initial_lease(tmp_path):
    database = tmp_path / "state.sqlite3"
    query = "Implement the project feature"
    with TaskStateStore(database) as store:
        task = store.create("main", tmp_path, query, route_chat_request(query), True)
        task = store.claim(task, "silent-check-worker", 1)
    heartbeat = TaskLeaseHeartbeat(database, task, seconds=1, interval=0.1)
    heartbeat.start()
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import time; time.sleep(1.5)"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        assert not result.stdout
        assert not heartbeat.lost
        with TaskStateStore(database) as store:
            assert store.owns(task)
            assert (
                store.finalize(task, "blocked", "synthetic check complete")
                == "projected"
            )
    finally:
        heartbeat.close()


def test_reconciliation_pages_survive_restart_without_starvation(tmp_path):
    database = tmp_path / "state.sqlite3"
    identities = set()
    with TaskStateStore(database) as store:
        for index in range(5):
            query = f"Implement the project feature {index}"
            task = store.create(
                f"pages-{index}", tmp_path, query, route_chat_request(query), True
            )
            task = store.claim(task, f"owner-{index}", 30)
            store.bind_execution(task, f"job-{index}", 1)
            identities.add(task.id)
    found = set()
    for _ in range(3):
        with TaskStateStore(database) as store:
            batch = store.linked_running(limit=2)
            assert len(batch) <= 2
            found.update(task.id for task, _, _ in batch)
    assert found == identities
