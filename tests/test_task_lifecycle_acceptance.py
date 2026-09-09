"""Isolated slow-call, concurrent takeover and abrupt-process recovery tests."""

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from context_agent.autopilot import AutopilotHeartbeat, AutopilotStore
from context_agent.routing import route_chat_request
from context_agent.task_lifecycle import TaskLeaseHeartbeat, reconcile_execution_states
from context_agent.task_state import TaskConflict, TaskStateStore


@pytest.mark.parametrize("repeat", range(2))
def test_dual_heartbeat_during_controlled_slow_call(tmp_path, repeat):
    database = tmp_path / "state.sqlite3"
    query = f"Implement the project feature {repeat}"
    with TaskStateStore(database) as state:
        task = state.create("slow", tmp_path, query, route_chat_request(query), True)
        task = state.claim(task, "slow-worker", 1)
    saved_heartbeat = TaskLeaseHeartbeat(database, task, seconds=1, interval=0.1)
    saved_heartbeat.start()
    try:
        with AutopilotStore(tmp_path / "jobs.sqlite3") as jobs:
            _, lease = jobs.start_or_resume(
                thread_id="slow",
                objective=query,
                workspace=tmp_path,
                allow_write=True,
                batch_size=1,
                lease_seconds=1,
                workflow="project-change",
                task_identity=task.id,
            )
            with TaskStateStore(database) as state:
                state.bind_execution(task, lease.job_id, lease.generation)
            with AutopilotHeartbeat(
                jobs, lease, lease_seconds=1, interval_seconds=0.1
            ) as heartbeat:
                # Controlled non-streaming transport: no model/tool progress.
                assert not threading.Event().wait(1.5)
                heartbeat.ensure_owned()
                assert not heartbeat.lease_lost
                with TaskStateStore(database) as state:
                    assert state.owns(task)
                    assert state.list("slow", tmp_path)[0].revision == task.revision
            jobs.mark_blocked(
                lease,
                error_code="synthetic_transport_timeout",
                safe_message="Controlled slow-call acceptance",
            )
        assert reconcile_execution_states(database, tmp_path / "jobs.sqlite3") == 1
        with TaskStateStore(database) as state:
            assert state.list("slow", tmp_path)[0].status == "blocked"
    finally:
        saved_heartbeat.close()


@pytest.mark.parametrize("repeat", range(2))
def test_concurrent_takeover_has_one_winner_and_fences_old_owner(
    tmp_path, monkeypatch, repeat
):
    now = [100.0]
    monkeypatch.setattr("context_agent.task_state.time.time", lambda: now[0])
    database = tmp_path / "state.sqlite3"
    query = f"Implement the project feature {repeat}"
    with TaskStateStore(database) as state:
        task = state.create("race", tmp_path, query, route_chat_request(query), True)
        old = state.claim(task, "old", 10)
    now[0] = 111
    barrier = threading.Barrier(2)

    def attempt(owner):
        with TaskStateStore(database) as state:
            barrier.wait(timeout=5)
            try:
                return state.claim(old, owner, 10)
            except TaskConflict:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = [task for task in pool.map(attempt, ("B", "C")) if task]
    assert len(winners) == 1
    with TaskStateStore(database) as state:
        assert not state.renew(old, 10)
        assert not state.finish(old, "completed", "late output")
        assert state.finalize(old, "blocked", "late terminal") == "superseded"
        assert state.owns(winners[0])
        assert winners[0].revision == old.revision + 1
        assert winners[0].allow_write == old.allow_write


_CRASH_WORKER = """
import os, sys
from pathlib import Path
from context_agent.autopilot import AutopilotStore
from context_agent.routing import route_chat_request
from context_agent.task_state import TaskStateStore
root = Path(sys.argv[1])
with TaskStateStore(root / 'state.sqlite3') as state:
    query = 'Implement isolated recovery fixture'
    task = state.create('crash', root, query, route_chat_request(query), True)
    task = state.claim(task, 'crashed-worker', 30)
    with AutopilotStore(root / 'jobs.sqlite3') as jobs:
        _, lease = jobs.start_or_resume(
            thread_id='crash', objective=query, workspace=root,
            allow_write=True, batch_size=1, workflow='project-change',
            task_identity=task.id)
        state.bind_execution(task, lease.job_id, lease.generation)
        jobs.mark_blocked(lease, error_code='controlled_crash',
                          safe_message='Isolated process recovery test')
        if sys.argv[2] == 'outbox':
            state.record_terminal(task, 'blocked', 'controlled_crash')
        os._exit(73)
"""


@pytest.mark.parametrize("repeat", range(2))
def test_explicit_handoff_preserves_scope_and_rejects_live_takeover(tmp_path, repeat):
    database = tmp_path / "state.sqlite3"
    query = f"Implement the project feature {repeat}"
    with TaskStateStore(database) as state:
        task = state.create("handoff", tmp_path, query, route_chat_request(query), True)
        first = state.claim(task, "first", 30)
        with pytest.raises(TaskConflict, match="still live"):
            state.recover_claim(first.id, "handoff", tmp_path, "intruder", 30)
        assert state.owns(first)
        assert (
            state.finalize(first, "partial", "Verified handoff checkpoint")
            == "projected"
        )
    with TaskStateStore(database) as state:
        checkpoint = state.list("handoff", tmp_path)[0]
        second = state.claim(checkpoint, "second", 30)
        assert second.id == first.id
        assert json.dumps(second.routing, sort_keys=True) == json.dumps(
            first.routing, sort_keys=True
        )
        assert second.workspace == first.workspace
        assert second.allow_write == first.allow_write
        assert second.evidence == "Verified handoff checkpoint"
        assert state.owns(second)
        assert not state.renew(first, 30)
        assert state.finalize(first, "completed", "late reply") == "superseded"


@pytest.mark.parametrize("repeat", range(2))
@pytest.mark.parametrize("point", ["job_commit", "outbox"])
def test_abrupt_process_exit_recovers_without_rerunning(tmp_path, point, repeat):
    result = subprocess.run(
        [sys.executable, "-c", _CRASH_WORKER, str(tmp_path), point],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 73, result.stderr
    database = tmp_path / "state.sqlite3"
    with TaskStateStore(database) as state:
        before = state.list("crash", tmp_path)[0]
        assert before.status == "running"
    assert reconcile_execution_states(database, tmp_path / "jobs.sqlite3") == 1
    assert reconcile_execution_states(database, tmp_path / "jobs.sqlite3") == 0
    with TaskStateStore(database) as state:
        final = state.list("crash", tmp_path)[0]
        assert final.id == before.id
        assert final.status == "blocked"
        assert final.owner is None
        assert final.revision == before.revision + 1
        assert "controlled_crash" in final.evidence
    with AutopilotStore(tmp_path / "jobs.sqlite3") as jobs:
        assert jobs.resumable_jobs(workspace=tmp_path) == []
