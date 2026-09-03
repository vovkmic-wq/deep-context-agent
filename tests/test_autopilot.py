"""Persistent autopilot orchestration and adaptive retry tests."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import SequenceChatModel
from langchain_core.messages import AIMessage

from context_agent.autopilot import (
    AutopilotHeartbeat,
    AutopilotLeaseError,
    AutopilotStore,
)
from context_agent.config import AppConfig, ProviderConfig
from context_agent.errors import AgentError, ConfigurationError
from context_agent.project_checks import ProjectCheckResult
from context_agent.runtime import AgentRuntime


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
        monkeypatch.setattr(runtime.project_check_runner, "run", lambda: [passed])
        result = runtime.run_autopilot_job(
            "Perform a complete project audit and implement confirmed fixes.",
            thread_id="verified-write",
            allow_write=True,
        )

    assert "verification=passed" in result
    with AutopilotStore(config.autopilot_database) as store:
        job = store.list_jobs(workspace=config.workspace)[0]
        details = store.details(str(job["id"]))
    assert details["status"] == "complete"
    assert details["verification_status"] == "passed"
    assert details["verification_results"][0]["check"] == "pytest"


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
