"""Real database contention and additive migration on disposable copies."""

import sqlite3
import threading
import time

import pytest

from context_agent.autopilot import AutopilotLeaseError, AutopilotStore
from context_agent.routing import route_chat_request
from context_agent.task_state import TaskStateStore


@pytest.mark.parametrize("release_lock", [True, False])
def test_job_renewal_busy_is_bounded_and_fenced(tmp_path, release_lock):
    database = tmp_path / "jobs.sqlite3"
    with AutopilotStore(database) as store:
        progress, lease = store.start_or_resume(
            thread_id="busy",
            objective="Verify project",
            workspace=tmp_path,
            allow_write=False,
            batch_size=1,
            lease_seconds=10,
        )
        acquired = threading.Event()
        unlock = threading.Event()

        def hold_lock():
            with sqlite3.connect(database) as connection:
                connection.execute("BEGIN IMMEDIATE")
                acquired.set()
                unlock.wait(0.2 if release_lock else 3)

        worker = threading.Thread(target=hold_lock)
        worker.start()
        assert acquired.wait(2)
        started = time.monotonic()
        try:
            if release_lock:
                store.renew_lease(lease, 10)
                store.assert_lease(lease)
            else:
                with pytest.raises(AutopilotLeaseError, match="storage_unavailable"):
                    store.renew_lease(lease, 10)
            assert time.monotonic() - started < 1.5
        finally:
            unlock.set()
            worker.join(3)
        current = store.progress(progress.job_id)
        assert current.lease_generation == lease.generation
        assert current.active_time_seconds == progress.active_time_seconds
        assert current.checkpoint_revision == progress.checkpoint_revision


def test_migration_of_copied_legacy_database_preserves_history(tmp_path):
    original = tmp_path / "legacy.sqlite3"
    copy = tmp_path / "migrated.sqlite3"
    with TaskStateStore(original) as store:
        query = "Implement fixture project"
        task = store.create("history", tmp_path, query, route_chat_request(query), True)
        task = store.claim(task, "legacy-owner", 10)
        with store.db:
            store.db.execute(
                "UPDATE authorized_tasks SET lease_until=1 WHERE id=?", (task.id,)
            )
            store.db.execute("CREATE TABLE preserved_history (content TEXT)")
            store.db.execute("INSERT INTO preserved_history VALUES ('OLD_HISTORY')")
            # Schema before terminal outbox/link/cursor introduction.
            for table in ("task_execution_links", "task_terminal_events"):
                store.db.execute(f"DROP TABLE {table}")
    with sqlite3.connect(original) as source, sqlite3.connect(copy) as destination:
        source.backup(destination)
    for repeat in range(2):
        with TaskStateStore(copy) as store:
            assert store.reconcile_unlinked_expired() == (1 if repeat == 0 else 0)
            saved = store.list("history", tmp_path)[0]
            assert saved.public()["status"] == "reconciliation_required"
            assert saved.objective == query
            assert saved.allow_write is True
            assert (
                store.db.execute("SELECT content FROM preserved_history").fetchone()[0]
                == "OLD_HISTORY"
            )
    with sqlite3.connect(original) as source:
        assert (
            source.execute("SELECT status FROM authorized_tasks").fetchone()[0]
            == "running"
        )
