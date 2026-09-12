"""Persistent autopilot orchestration and adaptive retry tests."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import SequenceChatModel, complete_check_results
from langchain_core.messages import AIMessage

from context_agent.autopilot import (
    AutopilotHeartbeat,
    AutopilotLeaseError,
    AutopilotRevisionError,
    AutopilotStore,
    NextOperation,
)
from context_agent.config import AppConfig, ProviderConfig
from context_agent.errors import AgentError, ConfigurationError
from context_agent.project_checks import ProjectCheckResult
from context_agent.reliability import ExecutionStopped
from context_agent.runtime import AgentRuntime


def test_cancel_during_verify_does_not_leave_running_job(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.prepare_directories()
    (config.workspace / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    with AgentRuntime(
        config, _provider(), model=SequenceChatModel(responses=[])
    ) as runtime:

        def cancelled(**kwargs):
            raise ExecutionStopped("task_cancelled")

        monkeypatch.setattr(runtime.project_check_runner, "run", cancelled)
        with pytest.raises(ExecutionStopped, match="task_cancelled"):
            runtime.run_autopilot_job(
                "Run verification only for /workspace",
                workflow="verification-only",
                thread_id="cancel-verifier",
                allow_write=False,
            )
        job = runtime.autopilot_store.list_jobs(workspace=config.workspace)[0]
        details = runtime.autopilot_store.details(str(job["id"]))
        assert job["status"] == "cancelled"
        assert job["last_error_code"] == "task_cancelled"
        assert all(unit["status"] != "running" for unit in details["work_units"])
        assert job["verification_status"] != "passed"


def test_abort_execution_cannot_overwrite_replacement(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("context_agent.autopilot.time.time", lambda: now[0])
    with AutopilotStore(tmp_path / "jobs.sqlite3") as store:
        arguments = dict(
            thread_id="abort-fence",
            objective="Verify project",
            workspace=tmp_path,
            allow_write=False,
            batch_size=1,
            lease_seconds=10,
        )
        _, old = store.start_or_resume(**arguments)
        now[0] = 111
        current, replacement = store.start_or_resume(**arguments)
        with pytest.raises(AutopilotLeaseError):
            store.abort_execution(old, error_code="task_cancelled")
        store.assert_lease(replacement)
        assert store.progress(current.job_id).status == "running"


def test_initial_heartbeat_failure_is_not_masked_by_unstarted_thread(
    tmp_path, monkeypatch
):
    with AgentRuntime(
        _config(tmp_path), _provider(), model=SequenceChatModel(responses=[])
    ) as runtime:

        def failed_renewal(*args, **kwargs):
            raise AutopilotLeaseError("synthetic first renewal failure")

        monkeypatch.setattr(runtime.autopilot_store, "renew_lease", failed_renewal)
        with pytest.raises(ExecutionStopped, match="task_authority_lost") as error:
            runtime.run_autopilot_job(
                "Verify fixture project", thread_id="failed-start"
            )
        assert error.value.reason == "job_lease_lost"
        assert "synthetic first renewal failure" in str(error.value.__cause__)


def _mock_verified_project(runtime, monkeypatch, passed):
    root = runtime.app_config.workspace
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n")

    def run(*, project_root):
        assert project_root == root.resolve()
        return complete_check_results(project_root)

    monkeypatch.setattr(runtime.project_check_runner, "run", run)


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        project_root=tmp_path,
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        context_root=tmp_path / "workspace",
        audit_batch_size=2,
        audit_max_batches_per_request=1,
        autopilot_max_work_units=20,
        autopilot_max_replans=4,
        autopilot_retry_attempts=2,
    )


def _provider() -> ProviderConfig:
    return ProviderConfig(
        name="lmstudio",
        model="test-model",
        base_url="http://127.0.0.1:1234/v1",
        api_key="test",
    )


def test_store_identity_lease_control_resume_and_report(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "data" / "autopilot.sqlite3"
    with AutopilotStore(database) as store:
        progress, lease = store.start_or_resume(
            thread_id="main",
            objective="Audit the project",
            workspace=workspace,
            allow_write=False,
            batch_size=8,
            include_patterns=("src/**",),
        )
        assert progress.status == "running"
        assert progress.mode == "read-only"
        with pytest.raises(RuntimeError, match="already running"):
            store.start_or_resume(
                thread_id="main",
                objective="Audit the project",
                workspace=workspace,
                allow_write=False,
                batch_size=8,
                include_patterns=("src/**",),
            )

        unit_id, sequence, worker_thread = store.begin_unit(
            lease,
            phase="audit",
            batch_size=8,
        )
        assert sequence == 1
        assert worker_thread.endswith(":unit:1")
        store.complete_unit(lease, unit_id, "one file reviewed")
        requested = store.set_control_status(progress.job_id, "paused")
        assert requested.status == "running"
        assert requested.requested_status == "paused"
        paused = store.honor_requested_control(lease)
        assert paused.status == "paused"

        resumed, resumed_lease = store.start_or_resume(
            thread_id="main",
            objective="Audit the project",
            workspace=workspace,
            allow_write=False,
            batch_size=4,
            include_patterns=("src/**",),
        )
        assert resumed.job_id == progress.job_id
        assert resumed.completed_units == 1
        completed = store.mark_complete(resumed_lease, "final report")
        assert completed.status == "complete"
        details = store.details(completed.job_id)
        assert details["report"] == "final report"
        assert details["include_patterns"] == ["src/**"]
        assert details["work_units"][0]["status"] == "complete"

        other_mode = AutopilotStore.job_id_for(
            thread_id="main",
            objective="Audit the project",
            workspace=workspace,
            allow_write=True,
            include_patterns=("src/**",),
        )
        assert other_mode != completed.job_id


def test_verification_only_resume_returns_to_verify_not_implement(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "data" / "autopilot.sqlite3"
    with AutopilotStore(database) as store:
        _, lease = store.start_or_resume(
            thread_id="verify-resume",
            objective="Only run checks",
            workspace=workspace,
            allow_write=False,
            batch_size=1,
            workflow="verification-only",
        )
        store.mark_blocked(
            lease,
            error_code="verification_failed",
            safe_message="A check failed.",
            blocker={"error_code": "verification_failed"},
        )
        resumed, _ = store.start_or_resume(
            thread_id="verify-resume",
            objective="Only run checks",
            workspace=workspace,
            allow_write=False,
            batch_size=1,
            workflow="verification-only",
        )

    assert resumed.phase == "verify"
    assert resumed.workflow == "verification-only"


def test_expired_generation_is_interrupted_and_stale_owner_is_fenced(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "data" / "autopilot.sqlite3"
    first = AutopilotStore(database)
    second = AutopilotStore(database)
    try:
        progress, stale_lease = first.start_or_resume(
            thread_id="restart",
            objective="Audit and repair requirements",
            workspace=workspace,
            allow_write=True,
            batch_size=2,
            lease_seconds=30,
        )
        stale_unit, _, _ = first.begin_unit(
            stale_lease,
            phase="audit",
            batch_size=2,
            deadline_seconds=60,
        )
        with pytest.raises(RuntimeError, match="already running"):
            second.start_or_resume(
                thread_id="restart",
                objective="Audit and repair requirements",
                workspace=workspace,
                allow_write=True,
                batch_size=1,
                lease_seconds=30,
            )
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE autopilot_jobs SET lease_until = 0 WHERE id = ?",
                (progress.job_id,),
            )

        resumed, current_lease = second.start_or_resume(
            thread_id="restart",
            objective="Audit and repair requirements",
            workspace=workspace,
            allow_write=True,
            batch_size=1,
            lease_seconds=30,
        )

        assert current_lease.generation == stale_lease.generation + 1
        assert resumed.interrupted_units == 1
        details = second.details(progress.job_id)
        interrupted = next(
            unit for unit in details["work_units"] if unit["id"] == stale_unit
        )
        assert interrupted["status"] == "interrupted"
        assert interrupted["error_code"] == "worker_interrupted"
        with pytest.raises(AutopilotLeaseError, match="lease was lost"):
            first.complete_unit(stale_lease, stale_unit, "stale completion")

        current_unit, sequence, _ = second.begin_unit(
            current_lease,
            phase="audit",
            batch_size=1,
        )
        assert sequence == 2
        second.complete_unit(current_lease, current_unit, "safe retry")
    finally:
        second.close()
        first.close()


def test_heartbeat_keeps_short_lease_alive_and_emits_progress(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "data" / "autopilot.sqlite3"
    events: list[bool] = []
    with AutopilotStore(database) as store:
        _, lease = store.start_or_resume(
            thread_id="heartbeat",
            objective="Long bounded unit",
            workspace=workspace,
            allow_write=False,
            batch_size=1,
            lease_seconds=1,
        )
        unit_id, _, _ = store.begin_unit(
            lease,
            phase="audit",
            batch_size=1,
            deadline_seconds=1,
        )
        heartbeat = AutopilotHeartbeat(
            store,
            lease,
            lease_seconds=1,
            interval_seconds=0.1,
            unit_id=unit_id,
            deadline_seconds=0.5,
            on_heartbeat=events.append,
        )
        with heartbeat:
            time.sleep(1.25)
            heartbeat.ensure_owned()
        store.complete_unit(lease, unit_id, "completed after original lease")
        details = store.details(lease.job_id)

    assert len(events) >= 5
    assert any(events)
    assert heartbeat.deadline_exceeded is True
    assert details["last_heartbeat_at"] is not None


def test_existing_database_is_migrated_without_recreation(tmp_path: Path) -> None:
    database = tmp_path / "data" / "autopilot.sqlite3"
    with AutopilotStore(database):
        pass
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE autopilot_jobs DROP COLUMN last_heartbeat_at")
        connection.execute("ALTER TABLE autopilot_jobs DROP COLUMN lease_generation")
        connection.execute("ALTER TABLE autopilot_jobs DROP COLUMN workflow")
        connection.execute("ALTER TABLE autopilot_work_units DROP COLUMN deadline_at")
        connection.execute(
            "ALTER TABLE autopilot_work_units DROP COLUMN last_heartbeat_at"
        )
        connection.execute(
            "ALTER TABLE autopilot_work_units DROP COLUMN lease_generation"
        )

    with AutopilotStore(database) as migrated:
        job_columns = {
            str(row["name"])
            for row in migrated._connection.execute("PRAGMA table_info(autopilot_jobs)")
        }
        unit_columns = {
            str(row["name"])
            for row in migrated._connection.execute(
                "PRAGMA table_info(autopilot_work_units)"
            )
        }

    assert {"lease_generation", "last_heartbeat_at", "workflow"} <= job_columns
    assert {"lease_generation", "last_heartbeat_at", "deadline_at"} <= unit_columns


def test_opening_store_recovers_expired_job_as_paused_interrupted(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "data" / "autopilot.sqlite3"
    with AutopilotStore(database) as store:
        progress, lease = store.start_or_resume(
            thread_id="crashed",
            objective="Resume after process crash",
            workspace=workspace,
            allow_write=False,
            batch_size=1,
            lease_seconds=30,
        )
        store.begin_unit(lease, phase="audit", batch_size=1)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE autopilot_jobs SET lease_until = 0 WHERE id = ?",
            (progress.job_id,),
        )

    with AutopilotStore(database) as recovered:
        current = recovered.progress(progress.job_id)
        details = recovered.details(progress.job_id)

    assert current.status == "paused"
    assert current.phase == "interrupted"
    assert current.interrupted_units == 1
    assert current.last_error_code == "autopilot_lease_expired"
    assert details["work_units"][0]["status"] == "interrupted"


def test_runtime_autopilot_replans_step_limit_and_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.prepare_directories()
    (config.workspace / "a.py").write_text("A = 1\n", encoding="utf-8")
    (config.workspace / "b.py").write_text("B = 2\n", encoding="utf-8")
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/a.py"},
                        "id": "read-a",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="a reviewed"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/b.py"},
                        "id": "read-b",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="b reviewed"),
        ]
    )
    events: list[str] = []

    with AgentRuntime(config, _provider(), model=model) as runtime:
        original = runtime.run_project_audit
        attempts = 0

        def flaky(*args: Any, **kwargs: Any) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise AgentError("GraphRecursionError: recursion limit reached")
            return original(*args, **kwargs)

        monkeypatch.setattr(runtime, "run_project_audit", flaky)
        result = runtime.run_autopilot_job(
            "Perform a full audit of the entire project.",
            thread_id="adaptive",
            progress_callback=lambda _job, _audit, event: events.append(event),
        )

    assert "Autopilot job complete" in result
    assert "replanned" in events
    with AutopilotStore(config.autopilot_database) as store:
        jobs = store.list_jobs(workspace=config.workspace)
        details = store.details(str(jobs[0]["id"]))
    progress = details["progress"]
    assert isinstance(progress, dict)
    assert progress["status"] == "complete"
    assert progress["batch_size"] == 1
    assert progress["replans"] == 1
    assert progress["attempts"] == 3
    assert details["id"] != details["audit_run_id"]
    assert len({unit["worker_thread_id"] for unit in details["work_units"]}) == 3


def test_conversational_autopilot_soft_yields_and_keeps_runtime_receipts(
    tmp_path: Path,
) -> None:
    config = replace(
        _config(tmp_path),
        autopilot_soft_model_calls_per_unit=2,
        autopilot_max_work_units=4,
    )
    config.prepare_directories()
    target = config.workspace / "pages.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {
                            "file_path": "/workspace/pages.txt",
                            "offset": 0,
                            "limit": 1,
                        },
                        "id": "page-one",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {
                            "file_path": "/workspace/pages.txt",
                            "offset": 1,
                            "limit": 1,
                        },
                        "id": "page-two",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Продолжение после безопасной передачи."),
        ]
    )
    events: list[str] = []

    with AgentRuntime(config, _provider(), model=model) as runtime:
        response = runtime.run_autopilot_job(
            "Прочитай нужные страницы проекта и продолжи задачу.",
            thread_id="soft-yield",
            workflow="project-change",
            progress_callback=lambda _job, _audit, event: events.append(event),
        )

    assert "Продолжение после безопасной передачи" in response
    assert "yielded" in events
    with AutopilotStore(config.autopilot_database) as store:
        job = store.list_jobs(workspace=config.workspace)[0]
        details = store.details(str(job["id"]))
    progress = details["progress"]
    assert progress["yielded_units"] == 1
    assert progress["file_reads"] == 2
    assert progress["unique_lines_read"] == 2
    ordered_units = sorted(details["work_units"], key=lambda unit: unit["sequence"])
    assert [unit["status"] for unit in ordered_units] == [
        "yielded",
        "complete",
    ]
    assert len(details["tool_receipts"]) == 2


def test_targeted_autopilot_soft_yields_do_not_consume_failure_retries(
    tmp_path: Path,
) -> None:
    config = replace(
        _config(tmp_path),
        autopilot_soft_model_calls_per_unit=2,
        autopilot_max_work_units=3,
        autopilot_retry_attempts=1,
    )
    config.prepare_directories()
    target = config.workspace / "target.txt"
    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {
                        "file_path": "/workspace/target.txt",
                        "offset": offset,
                        "limit": 1,
                    },
                    "id": f"target-page-{offset}",
                    "type": "tool_call",
                }
            ],
        )
        for offset in range(4)
    ]
    responses.append(AIMessage(content="Targeted work completed."))

    with AgentRuntime(
        config, _provider(), model=SequenceChatModel(responses=responses)
    ) as runtime:
        response = runtime.run_autopilot_job(
            "Прочитай точные страницы /workspace/target.txt.",
            thread_id="targeted-soft-yield",
            workflow="targeted-change",
        )

    assert "Targeted work completed" in response
    with AutopilotStore(config.autopilot_database) as store:
        details = store.details(
            str(store.list_jobs(workspace=config.workspace)[0]["id"])
        )
    progress = details["progress"]
    assert progress["status"] == "complete"
    assert progress["yielded_units"] == 2
    assert progress["failed_units"] == 0


def test_conversational_work_unit_ceiling_blocks_instead_of_asserting(
    tmp_path: Path,
) -> None:
    config = replace(
        _config(tmp_path),
        autopilot_soft_model_calls_per_unit=2,
        autopilot_max_work_units=2,
    )
    config.prepare_directories()
    (config.workspace / "limit.txt").write_text(
        "one\ntwo\nthree\nfour\n", encoding="utf-8"
    )
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {
                        "file_path": "/workspace/limit.txt",
                        "offset": offset,
                        "limit": 1,
                    },
                    "id": f"limit-page-{offset}",
                    "type": "tool_call",
                }
            ],
        )
        for offset in range(4)
    ]

    with AgentRuntime(
        config, _provider(), model=SequenceChatModel(responses=responses)
    ) as runtime:
        response = runtime.run_autopilot_job(
            "Прочитай страницы /workspace/limit.txt.",
            thread_id="work-unit-ceiling",
            workflow="targeted-change",
        )

    assert "work_unit_limit_exhausted" in response
    with AutopilotStore(config.autopilot_database) as store:
        progress = store.progress(
            str(store.list_jobs(workspace=config.workspace)[0]["id"])
        )
    assert progress.status == "blocked"
    assert progress.yielded_units == 2


def test_runtime_emits_heartbeat_during_model_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(
        _config(tmp_path),
        audit_batch_size=1,
        autopilot_unit_batch_size=1,
        autopilot_heartbeat_seconds=1,
    )
    config.prepare_directories()
    (config.workspace / "slow.py").write_text("VALUE = 1\n", encoding="utf-8")
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/slow.py"},
                        "id": "read-slow",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="slow.py reviewed"),
        ]
    )
    events: list[str] = []
    with AgentRuntime(config, _provider(), model=model) as runtime:
        original = runtime.run_project_audit

        def delayed(*args: Any, **kwargs: Any) -> str:
            time.sleep(2.2)
            return original(*args, **kwargs)

        monkeypatch.setattr(runtime, "run_project_audit", delayed)
        result = runtime.run_autopilot_job(
            "Perform a full project audit.",
            thread_id="slow-heartbeat",
            progress_callback=lambda _job, _audit, event: events.append(event),
        )

    assert "Autopilot job complete" in result
    assert events.count("heartbeat") >= 2


def test_stale_autopilot_owner_cannot_mutate_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(_config(tmp_path), audit_batch_size=1)
    config.prepare_directories()
    (config.workspace / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/workspace/stale.py",
                            "content": "STALE = True\n",
                        },
                        "id": "stale-write",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )

    with AgentRuntime(config, _provider(), model=model) as runtime:
        monkeypatch.setattr(
            runtime.autopilot_store,
            "assert_lease",
            lambda _lease: (_ for _ in ()).throw(
                AutopilotLeaseError("Autopilot job lease was lost")
            ),
        )
        with pytest.raises(AgentError, match="lease ownership"):
            runtime.run_autopilot_job(
                "Audit and fix every project module.",
                thread_id="stale-owner",
                allow_write=True,
            )

    assert not (config.workspace / "stale.py").exists()


def test_autopilot_configuration_bounds(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        replace(_config(tmp_path), autopilot_max_work_units=0)


def test_durable_budgets_structured_operation_and_revision_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic_values = iter((100.0, 101.0))
    monkeypatch.setattr(
        "context_agent.autopilot.time.monotonic",
        lambda: next(monotonic_values),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with AutopilotStore(tmp_path / "data" / "autopilot.sqlite3") as store:
        job_id = store.enqueue(
            thread_id="durable",
            objective="Implement one bounded change",
            workspace=workspace,
            allow_write=True,
            batch_size=1,
            workflow="project-change",
            active_time_limit_seconds=120,
            wall_time_limit_seconds=600,
        )
        queued = store.progress(job_id)
        assert queued.status == "queued"
        assert queued.phase == "discover"
        running, lease = store.start_or_resume(
            thread_id="durable",
            objective="Implement one bounded change",
            workspace=workspace,
            allow_write=True,
            batch_size=1,
            workflow="project-change",
            active_time_limit_seconds=120,
            wall_time_limit_seconds=600,
        )
        store.set_next_operation(
            lease,
            {
                "phase": "implement",
                "operation": "create_file",
                "target": "/workspace/new.py",
                "objective": "Create the implementation file",
                "required_evidence_ids": [],
                "expected_effect": "new file",
                "verification_commands": ["python -m pytest -q"],
                "blocking_conditions": [],
            },
        )
        unit_id, _, _ = store.begin_unit(
            lease,
            phase="discover",
            batch_size=1,
        )
        store.yield_unit(lease, unit_id, "handoff with evidence")
        progress = store.progress(job_id)
        assert progress.active_time_seconds > 0
        assert progress.next_operation_data is not None
        assert progress.next_operation_data["target"] == "/workspace/new.py"
        assert progress.discovery_units == 1
        stale_revision = running.checkpoint_revision
        with pytest.raises(AutopilotRevisionError, match="revision is stale"):
            store.set_control_status(
                job_id,
                "paused",
                expected_revision=stale_revision,
            )
        with store._connection:
            store._connection.execute(
                "UPDATE autopilot_jobs SET active_time_seconds = "
                "active_time_limit_seconds WHERE id = ?",
                (job_id,),
            )
        assert store.budget_error(job_id) == "task_active_time_exhausted"

        with pytest.raises(ValueError, match="escapes workspace"):
            store.set_next_operation(
                lease,
                {
                    "phase": "implement",
                    "operation": "edit_file",
                    "target": str(tmp_path / "outside.py"),
                    "objective": "Unsafe target",
                },
            )


def test_mutating_next_operation_requires_target_effect_and_verification(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    base: dict[str, object] = {
        "phase": "implement",
        "operation": "edit_file",
        "objective": "Update one file",
        "expected_effect": "updated implementation",
        "verification_commands": ["run_project_checks"],
    }
    with pytest.raises(ValueError, match="concrete target"):
        NextOperation.parse(base, workspace)
    with pytest.raises(ValueError, match="expected effect"):
        NextOperation.parse(
            {**base, "target": "/workspace/source.py", "expected_effect": ""},
            workspace,
        )
    with pytest.raises(ValueError, match="verification plan"):
        NextOperation.parse(
            {**base, "target": "/workspace/source.py", "verification_commands": []},
            workspace,
        )


def test_project_change_advances_discover_implement_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.prepare_directories()
    (config.workspace / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/source.py"},
                        "id": "discover-source",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Minimal discovery complete."),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "file_path": "/workspace/implemented.py",
                            "content": "IMPLEMENTED = True\n",
                        },
                        "id": "implement-file",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Implementation complete."),
        ]
    )
    passed = ProjectCheckResult(
        check="pytest",
        command=("python", "-m", "pytest"),
        return_code=0,
        duration_seconds=0.01,
        status="passed",
        output="1 passed",
    )

    with AgentRuntime(config, _provider(), model=model) as runtime:
        _mock_verified_project(runtime, monkeypatch, passed)
        response = runtime.run_autopilot_job(
            "Inspect source.py, implement implemented.py and verify the project.",
            thread_id="phase-machine",
            workflow="project-change",
            allow_write=True,
        )

    assert "complete" in response
    assert (config.workspace / "implemented.py").read_text(encoding="utf-8") == (
        "IMPLEMENTED = True\n"
    )
    with AutopilotStore(config.autopilot_database) as store:
        details = store.details(
            str(store.list_jobs(workspace=config.workspace)[0]["id"])
        )
    assert details["status"] == "complete"
    assert details["verification_status"] == "passed"
    phases = {unit["phase"] for unit in details["work_units"]}
    assert {"discover", "implement", "verify"} <= phases


def test_allow_write_job_requires_current_verification_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.prepare_directories()
    (config.workspace / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/main.py"},
                        "id": "read-main",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="No confirmed changes required."),
        ]
    )
    passed = ProjectCheckResult(
        check="pytest",
        command=("python", "-m", "pytest"),
        return_code=0,
        duration_seconds=0.01,
        status="passed",
        output="1 passed",
    )

    with AgentRuntime(config, _provider(), model=model) as runtime:
        _mock_verified_project(runtime, monkeypatch, passed)
        result = runtime.run_autopilot_job(
            "Perform a complete project audit and implement confirmed fixes.",
            thread_id="verified-write",
            include_patterns=("*.py",),
            allow_write=True,
        )

    assert "verification=passed" in result
    with AutopilotStore(config.autopilot_database) as store:
        job = store.list_jobs(workspace=config.workspace)[0]
        details = store.details(str(job["id"]))
    assert details["status"] == "complete"
    assert details["verification_status"] == "passed"
    assert [item["check"] for item in details["verification_results"]] == [
        "ruff_check",
        "ruff_format_check",
        "pytest",
        "compileall",
    ]


def test_persistent_log_analysis_does_not_create_project_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.prepare_directories()
    model = SequenceChatModel(
        responses=[AIMessage(content="The log shows a provider timeout.")]
    )

    with AgentRuntime(config, _provider(), model=model) as runtime:
        runtime.set_routing_scope(
            workspace_reads_allowed=False,
            project_scan_allowed=False,
        )
        monkeypatch.setattr(
            runtime,
            "run_project_audit",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("log workflow must not start project audit")
            ),
        )
        result = runtime.run_autopilot_job(
            "Analyze this log: provider timed out.",
            thread_id="persistent-log",
            workflow="log-analysis",
        )

    assert result.startswith("The log shows a provider timeout.")
    with AutopilotStore(config.autopilot_database) as store:
        jobs = store.list_jobs(workspace=config.workspace)
        details = store.details(str(jobs[0]["id"]))
    assert details["workflow"] == "log-analysis"
    assert details["audit_run_id"] is None
    assert details["phase"] == "complete"
    assert details["work_units"][0]["phase"] == "execute"


def test_duplicate_discovery_evidence_does_not_renew_progress(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with AutopilotStore(tmp_path / "autopilot.sqlite3") as store:
        progress, lease = store.start_or_resume(
            thread_id="deduplicated-evidence",
            objective="Inspect one known file",
            workspace=workspace,
            allow_write=True,
            batch_size=1,
            workflow="project-change",
        )
        receipt = {
            "name": "read_file",
            "path": "/workspace/source.py",
            "status": "success",
            "content_version": "version-1",
            "start_line": 1,
            "end_line": 100,
            "bytes_returned": 500,
        }
        first_unit, _, _ = store.begin_unit(
            lease,
            phase="discover",
            batch_size=1,
        )
        store.record_tool_receipt(lease, first_unit, receipt)
        store.yield_unit(lease, first_unit, "first evidence handoff")
        first = store.progress(progress.job_id)
        assert first.file_reads == 1
        assert first.unique_lines_read == 100
        assert first.renewal_reason == "verified-handoff"

        second_unit, _, _ = store.begin_unit(
            lease,
            phase="discover",
            batch_size=1,
        )
        store.record_tool_receipt(lease, second_unit, receipt)
        store.yield_unit(lease, second_unit, "duplicate evidence handoff")
        duplicate = store.progress(progress.job_id)

    assert duplicate.file_reads == 1
    assert duplicate.unique_lines_read == 100
    assert duplicate.discovery_units == 2
    assert duplicate.renewal_reason == "no-verified-delta"


def test_invalid_phase_transition_is_rejected_and_recorded_transitions_are_visible(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with AutopilotStore(tmp_path / "autopilot.sqlite3") as store:
        progress, lease = store.start_or_resume(
            thread_id="phase-validation",
            objective="Implement a bounded change",
            workspace=workspace,
            allow_write=True,
            batch_size=1,
            workflow="project-change",
        )
        with pytest.raises(ValueError, match="discover -> verify"):
            store.transition_phase(lease, "verify", reason="invalid shortcut")
        store.transition_phase(lease, "plan", reason="evidence-covered")
        store.transition_phase(lease, "implement", reason="operation-ready")
        details = store.details(progress.job_id)

    assert [item["to_phase"] for item in reversed(details["phase_transitions"])] == [
        "plan",
        "implement",
    ]


def test_incident_shape_forces_implementation_instead_of_legacy_task_deadline(
    tmp_path: Path,
) -> None:
    config = replace(
        _config(tmp_path),
        task_timeout_seconds=10,
        autopilot_soft_model_calls_per_unit=2,
        discovery_max_units=2,
        autopilot_retry_attempts=4,
    )
    config.prepare_directories()
    (config.workspace / "first.py").write_text("FIRST = 1\n", encoding="utf-8")
    (config.workspace / "second.py").write_text("SECOND = 2\n", encoding="utf-8")
    (config.workspace / "third.py").write_text("THIRD = 3\n", encoding="utf-8")
    (config.workspace / "fourth.py").write_text("FOURTH = 4\n", encoding="utf-8")
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/first.py"},
                        "id": "discover-first",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/second.py"},
                        "id": "discover-second",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/third.py"},
                        "id": "discover-third",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/fourth.py"},
                        "id": "discover-fourth",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="I cannot identify a safe mutation."),
        ]
    )

    with AgentRuntime(config, _provider(), model=model) as runtime:
        response = runtime.run_autopilot_job(
            "Inspect the two files and implement the required safe change.",
            thread_id="incident-regression",
            workflow="project-change",
            allow_write=True,
        )

    with AutopilotStore(config.autopilot_database) as store:
        job = store.list_jobs(workspace=config.workspace)[0]
        details = store.details(str(job["id"]))
    assert "implementation_no_mutation" in response
    assert details["status"] == "blocked"
    assert details["last_error_code"] == "implementation_no_mutation"
    assert details["blocker_json"]["category"] == "implementation_result_missing"
    assert details["blocker_json"]["attempted_operation"]["target"]
    assert details["progress"]["discovery_units"] == 2
    assert details["progress"]["yielded_units"] == 2
    assert details["last_error_code"] != "task_deadline"
    assert {unit["phase"] for unit in details["work_units"]} == {
        "discover",
        "implement",
    }


def test_project_change_uses_validated_read_target_then_verifies_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(
        _config(tmp_path),
        discovery_max_units=1,
        autopilot_soft_model_calls_per_unit=2,
    )
    config.prepare_directories()
    for name in (
        "source.py",
        "dependency.py",
        "extra.py",
    ):
        (config.workspace / name).write_text(f"NAME = {name!r}\n", encoding="utf-8")
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/source.py"},
                        "id": "initial-discovery",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/extra.py"},
                        "id": "targeted-extra",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "edit_file",
                        "args": {
                            "file_path": "/workspace/extra.py",
                            "old_string": "NAME = 'extra.py'\n",
                            "new_string": "NAME = 'implemented'\n",
                        },
                        "id": "implementation-mutation",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )
    passed = ProjectCheckResult(
        check="pytest",
        command=("python", "-m", "pytest"),
        return_code=0,
        duration_seconds=0.01,
        status="passed",
        output="1 passed",
    )

    with AgentRuntime(config, _provider(), model=model) as runtime:
        _mock_verified_project(runtime, monkeypatch, passed)
        response = runtime.run_autopilot_job(
            "Implement the required safe change after targeted discovery.",
            thread_id="targeted-discovery",
            workflow="project-change",
            allow_write=True,
        )

    with AutopilotStore(config.autopilot_database) as store:
        details = store.details(
            str(store.list_jobs(workspace=config.workspace)[0]["id"])
        )
    assert "complete" in response
    assert details["progress"]["targeted_discovery_units"] == 0
    assert {unit["phase"] for unit in details["work_units"]} == {
        "discover",
        "implement",
        "verify",
    }
    assert details["next_operation_json"]["target"] == "/workspace/extra.py"
    assert (config.workspace / "extra.py").read_text(encoding="utf-8") == (
        "NAME = 'implemented'\n"
    )


def test_empty_planner_target_is_blocked_before_implementation_worker(
    tmp_path: Path,
) -> None:
    config = replace(
        _config(tmp_path),
        discovery_max_units=1,
        targeted_discovery_max_units=0,
    )
    config.prepare_directories()
    model = SequenceChatModel(responses=[AIMessage(content="No target discovered.")])

    with AgentRuntime(config, _provider(), model=model) as runtime:
        response = runtime.run_autopilot_job(
            "Implement service, CLI, FastAPI, UI and tests.",
            thread_id="empty-target-preflight",
            workflow="project-change",
            allow_write=True,
        )

    with AutopilotStore(config.autopilot_database) as store:
        details = store.details(
            str(store.list_jobs(workspace=config.workspace)[0]["id"])
        )
    assert "planner_contract_invalid" in response
    assert details["last_error_code"] == "planner_contract_invalid"
    assert details["progress"]["phase_attempts"] == 0
    assert details["progress"]["blocker"]["missing_prerequisites"]
    assert details["blocker_json"]["attempted_operation"]["target"] == ""
    assert [unit["phase"] for unit in details["work_units"]] == ["discover"]


def test_implementation_cannot_mutate_outside_validated_target(
    tmp_path: Path,
) -> None:
    config = replace(
        _config(tmp_path),
        discovery_max_units=1,
        targeted_discovery_max_units=0,
        autopilot_soft_model_calls_per_unit=2,
    )
    config.prepare_directories()
    (config.workspace / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
    (config.workspace / "other.py").write_text("VALUE = 2\n", encoding="utf-8")
    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/workspace/target.py"},
                        "id": "discover-target",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "edit_file",
                        "args": {
                            "file_path": "/workspace/other.py",
                            "old_string": "VALUE = 2\n",
                            "new_string": "VALUE = 3\n",
                        },
                        "id": "wrong-target",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Could not apply the change."),
        ]
    )

    with AgentRuntime(config, _provider(), model=model) as runtime:
        response = runtime.run_autopilot_job(
            "Update the implementation after reading its target.",
            thread_id="validated-mutation-target",
            workflow="project-change",
            allow_write=True,
        )

    with AutopilotStore(config.autopilot_database) as store:
        details = store.details(
            str(store.list_jobs(workspace=config.workspace)[0]["id"])
        )

    assert "implementation_no_mutation" in response
    assert (config.workspace / "other.py").read_text(encoding="utf-8") == (
        "VALUE = 2\n"
    )
    assert details["next_operation_json"]["target"] == "/workspace/target.py"
    with AutopilotStore(config.autopilot_database) as store:
        assert store.verified_mutation_count(str(details["id"])) == 0


def test_verification_only_starts_at_verify_and_never_calls_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.prepare_directories()
    nested = config.workspace / "ozon_market_analytics"
    nested.mkdir()
    (nested / "pyproject.toml").write_text("[project]\nname='ozon'\n")
    model = SequenceChatModel(responses=[AIMessage(content="must not be called")])
    roots: list[Path | None] = []

    def run_checks(
        _checks: str = "",
        *,
        project_root: Path | None = None,
    ) -> list[ProjectCheckResult]:
        roots.append(project_root)
        return complete_check_results(project_root)

    with AgentRuntime(config, _provider(), model=model) as runtime:
        monkeypatch.setattr(runtime.project_check_runner, "run", run_checks)
        response = runtime.run_autopilot_job(
            "Только заверши проверки уже написанного кода в "
            "/workspace/ozon_market_analytics без аудита.",
            thread_id="verification-only-pass",
            workflow="verification-only",
            allow_write=False,
        )

    with AutopilotStore(config.autopilot_database) as store:
        details = store.details(
            str(store.list_jobs(workspace=config.workspace)[0]["id"])
        )
    assert "complete" in response
    assert model.generation_attempts == 0
    assert roots == [nested.resolve()]
    assert [unit["phase"] for unit in details["work_units"]] == ["verify"]
    assert details["verification_status"] == "passed"


def test_verification_only_ambiguous_root_blocks_with_structure(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.prepare_directories()
    for name in ("first", "second"):
        project = config.workspace / name
        project.mkdir()
        (project / "pyproject.toml").write_text(f"[project]\nname='{name}'\n")
    model = SequenceChatModel(responses=[AIMessage(content="must not be called")])

    with AgentRuntime(config, _provider(), model=model) as runtime:
        response = runtime.run_autopilot_job(
            "Только заверши проверки уже написанного кода без аудита.",
            thread_id="verification-only-ambiguous",
            workflow="verification-only",
            allow_write=False,
        )

    with AutopilotStore(config.autopilot_database) as store:
        details = store.details(
            str(store.list_jobs(workspace=config.workspace)[0]["id"])
        )
    assert "verification_project_root_ambiguous" in response
    assert model.generation_attempts == 0
    assert details["status"] == "blocked"
    assert details["blocker_json"]["category"] == "verification_prerequisite"
    assert details["blocker_json"]["missing_prerequisites"]


def test_verification_only_failure_without_write_never_enters_implement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.prepare_directories()
    (config.workspace / "pyproject.toml").write_text("[project]\nname='demo'\n")
    model = SequenceChatModel(responses=[AIMessage(content="must not be called")])
    failed = ProjectCheckResult(
        check="pytest",
        command=("python", "-m", "pytest"),
        return_code=1,
        duration_seconds=0.01,
        status="failed",
        output="tests/test_demo.py:3: AssertionError",
    )

    with AgentRuntime(config, _provider(), model=model) as runtime:
        monkeypatch.setattr(
            runtime.project_check_runner,
            "run",
            lambda **_kwargs: [failed],
        )
        response = runtime.run_autopilot_job(
            "Только заверши проверки уже написанного кода без аудита.",
            thread_id="verification-only-failed",
            workflow="verification-only",
            allow_write=False,
        )

    with AutopilotStore(config.autopilot_database) as store:
        details = store.details(
            str(store.list_jobs(workspace=config.workspace)[0]["id"])
        )
    assert "verification_failed" in response
    assert model.generation_attempts == 0
    assert {unit["phase"] for unit in details["work_units"]} == {"verify"}
    assert details["blocker_json"]["evidence_ids"] == ["pytest"]


def test_recent_manifest_read_is_not_inferred_as_mutation_target() -> None:
    assert AgentRuntime._protected_inferred_mutation_target("/workspace/pyproject.toml")
    assert not AgentRuntime._protected_inferred_mutation_target(
        "/workspace/src/service.py"
    )


def test_verification_seed_path_stops_before_following_prose(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.prepare_directories()
    model = SequenceChatModel(responses=[AIMessage(content="unused")])
    with AgentRuntime(config, _provider(), model=model) as runtime:
        seeds = runtime._verification_seed_paths(
            "Проверь /workspace/ozon_market_analytics без повторного аудита."
        )

    assert seeds == ("/workspace/ozon_market_analytics",)


def test_verification_repair_target_requires_one_existing_evidence_file(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.prepare_directories()
    project = config.workspace / "nested"
    target = project / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n")
    model = SequenceChatModel(responses=[AIMessage(content="unused")])
    evidence = [
        {
            "check": "ruff_check",
            "status": "failed",
            "output": "src/service.py:1:1: E999 invalid syntax",
        }
    ]

    with AgentRuntime(config, _provider(), model=model) as runtime:
        resolved = runtime._verification_repair_target(evidence, project)

    assert resolved == "/workspace/nested/src/service.py"


def test_legacy_autopilot_database_migrates_additively_without_losing_job(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "autopilot.sqlite3"
    with AutopilotStore(database) as store:
        job_id = store.enqueue(
            thread_id="legacy-migration",
            objective="Preserve this queued job",
            workspace=workspace,
            allow_write=False,
            batch_size=1,
        )

    with sqlite3.connect(database) as connection:
        connection.execute("DROP INDEX IF EXISTS idx_autopilot_receipts_evidence")
        connection.execute("DROP TABLE IF EXISTS autopilot_phase_transitions")
        for column in (
            "task_identity",
            "next_operation_json",
            "resume_phase",
            "model_turn_timeout_seconds",
            "unit_timeout_seconds",
            "active_time_seconds",
            "active_time_limit_seconds",
            "wall_time_limit_seconds",
            "wall_deadline_at",
            "last_progress_kind",
            "last_progress_at",
            "renewal_reason",
            "blocker_json",
        ):
            connection.execute(f"ALTER TABLE autopilot_jobs DROP COLUMN {column}")
        for column in ("evidence_fingerprint", "verified_progress"):
            connection.execute(
                f"ALTER TABLE autopilot_tool_receipts DROP COLUMN {column}"
            )

    with AutopilotStore(database) as migrated:
        details = migrated.details(job_id)
        job_columns = {
            str(row["name"])
            for row in migrated._connection.execute(
                "PRAGMA table_info(autopilot_jobs)"
            ).fetchall()
        }
        receipt_columns = {
            str(row["name"])
            for row in migrated._connection.execute(
                "PRAGMA table_info(autopilot_tool_receipts)"
            ).fetchall()
        }

    assert details["thread_id"] == "legacy-migration"
    assert details["status"] == "queued"
    assert {
        "task_identity",
        "next_operation_json",
        "resume_phase",
        "active_time_seconds",
        "wall_deadline_at",
        "blocker_json",
    } <= job_columns
    assert {"evidence_fingerprint", "verified_progress"} <= receipt_columns


def test_crash_recovery_counts_heartbeat_active_time_not_process_downtime(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "autopilot.sqlite3"
    with AutopilotStore(database) as store:
        progress, lease = store.start_or_resume(
            thread_id="crash-active-time",
            objective="Resume after a crash",
            workspace=workspace,
            allow_write=True,
            batch_size=1,
            workflow="project-change",
        )
        unit_id, _, _ = store.begin_unit(
            lease,
            phase="discover",
            batch_size=1,
        )
        with store._connection:
            store._connection.execute(
                "UPDATE autopilot_work_units SET started_at = 100, "
                "last_heartbeat_at = 110 WHERE id = ?",
                (unit_id,),
            )
            store._connection.execute(
                "UPDATE autopilot_jobs SET lease_until = 1 WHERE id = ?",
                (progress.job_id,),
            )

    with AutopilotStore(database) as recovered:
        after = recovered.progress(progress.job_id)

    assert after.status == "paused"
    assert after.phase == "interrupted"
    assert after.active_time_seconds == 10
    assert after.interrupted_units == 1


def test_crash_after_mutation_receipt_reconciles_to_verify_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.prepare_directories()
    objective = "Create result.py and verify it"
    result_file = config.workspace / "result.py"
    result_file.write_text("RESULT = True\n", encoding="utf-8")
    with AutopilotStore(config.autopilot_database) as store:
        progress, lease = store.start_or_resume(
            thread_id="crash-after-mutation",
            objective=objective,
            workspace=config.workspace,
            allow_write=True,
            batch_size=1,
            workflow="project-change",
        )
        store.transition_phase(lease, "plan", reason="covered")
        store.transition_phase(lease, "implement", reason="ready")
        unit_id, _, _ = store.begin_unit(
            lease,
            phase="implement",
            batch_size=1,
        )
        store.record_tool_receipt(
            lease,
            unit_id,
            {
                "name": "write_file",
                "path": "/workspace/result.py",
                "status": "success",
                "content_sha256": "a" * 64,
            },
        )
        with store._connection:
            store._connection.execute(
                "UPDATE autopilot_jobs SET lease_until = 1 WHERE id = ?",
                (progress.job_id,),
            )

    model = SequenceChatModel(
        responses=[AIMessage(content="This response must never be requested.")]
    )
    passed = ProjectCheckResult(
        check="pytest",
        command=("python", "-m", "pytest"),
        return_code=0,
        duration_seconds=0.01,
        status="passed",
        output="1 passed",
    )
    with AgentRuntime(config, _provider(), model=model) as runtime:
        _mock_verified_project(runtime, monkeypatch, passed)
        response = runtime.run_autopilot_job(
            objective,
            thread_id="crash-after-mutation",
            workflow="project-change",
            allow_write=True,
        )

    with AutopilotStore(config.autopilot_database) as store:
        details = store.details(progress.job_id)
    assert "complete" in response
    assert model.generation_attempts == 0
    assert details["status"] == "complete"
    assert details["progress"]["interrupted_units"] == 1
    assert result_file.read_text(encoding="utf-8") == "RESULT = True\n"
