"""Persistent autopilot orchestration and adaptive retry tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import SequenceChatModel
from langchain_core.messages import AIMessage

from context_agent.autopilot import AutopilotStore
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
