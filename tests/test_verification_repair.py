"""Safety tests for explicitly approved verification repair tasks."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_agent.autopilot import AutopilotStore, NextOperation
from context_agent.repair import RepairConflictError, VerificationRepairStore


def _failed_source(database: Path, workspace: Path) -> tuple[str, int]:
    project = workspace / "ozon_market_analytics"
    target = project / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("value=1\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "[project]\nname='sample'\n", encoding="utf-8"
    )
    with AutopilotStore(database) as store:
        progress, lease = store.start_or_resume(
            thread_id="repair-test",
            objective="Verify the project without modifying it.",
            workspace=workspace,
            allow_write=False,
            batch_size=1,
            workflow="verification-only",
        )
        store.record_verification(
            lease,
            status="failed",
            results=[
                {
                    "check": "ruff_check",
                    "command": ["python", "-m", "ruff", "check", "."],
                    "return_code": 1,
                    "status": "failed",
                    "duration_seconds": 0.2,
                    "output": "src/app.py:1:6: E225 missing whitespace",
                }
            ],
        )
        blocked = store.mark_blocked(
            lease,
            error_code="verification_failed",
            safe_message="One configured check failed.",
            blocker={
                "category": "verification_failure",
                "attempted_operation": {
                    "operation": "run_project_checks",
                    "target": "/workspace/ozon_market_analytics/src/app.py",
                },
            },
        )
    return progress.job_id, blocked.checkpoint_revision


def test_failed_read_only_verification_creates_bounded_proposal(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    database = tmp_path / "autopilot.sqlite3"
    source_job_id, revision = _failed_source(database, workspace)

    with VerificationRepairStore(database, workspace=workspace) as store:
        proposals = store.proposals(source_job_id)
        repeated = store.proposals(source_job_id)

    assert len(proposals) == 1
    assert repeated[0].id == proposals[0].id
    assert proposals[0].source_revision == revision
    assert proposals[0].project_root == "/workspace/ozon_market_analytics"
    assert proposals[0].allowed_paths == (
        "/workspace/ozon_market_analytics/src/app.py",
    )
    assert proposals[0].evidence[0]["check"] == "ruff_check"


def test_approval_is_idempotent_and_write_gate_is_exact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    database = tmp_path / "autopilot.sqlite3"
    source_job_id, _revision = _failed_source(database, workspace)
    repair_job_id = "a" * 32
    repair_task_id = "b" * 32

    with VerificationRepairStore(database, workspace=workspace) as store:
        proposal = store.proposals(source_job_id)[0]
        first = store.reserve(
            proposal,
            idempotency_key="repair-once",
            repair_task_id=repair_task_id,
            repair_job_id=repair_job_id,
            confirmed_by="test-user",
        )
        second = store.reserve(
            proposal,
            idempotency_key="repair-once",
            repair_task_id="c" * 32,
            repair_job_id="d" * 32,
            confirmed_by="test-user",
        )
        store.assert_write(
            repair_job_id,
            "/workspace/ozon_market_analytics/src/app.py",
        )
        with pytest.raises(RepairConflictError, match="approved file territory"):
            store.assert_write(
                repair_job_id,
                "/workspace/ozon_market_analytics/pyproject.toml",
            )

    assert first["id"] == second["id"]
    assert second["repair_task_id"] == repair_task_id


def test_repair_job_starts_at_repair_with_concrete_operation(tmp_path: Path) -> None:
    database = tmp_path / "autopilot.sqlite3"
    workspace = tmp_path / "workspace"
    with AutopilotStore(database) as store:
        job_id = store.enqueue(
            thread_id="repair",
            objective="Repair one approved failure.",
            workspace=workspace,
            allow_write=True,
            batch_size=1,
            workflow="project-change",
            task_identity="f" * 32,
        )
        store.prepare_repair_job(
            job_id,
            NextOperation(
                phase="repair",
                operation="edit_file",
                target="/workspace/src/app.py",
                objective="Fix the reported Ruff violation.",
                required_evidence_ids=("evidence-1",),
                expected_effect="Ruff passes.",
                verification_commands=("ruff_check",),
            ),
        )
        details = store.details(job_id)

    assert details["phase"] == "repair"
    assert details["next_operation_json"]["target"] == "/workspace/src/app.py"


def test_approved_repair_is_invalidated_when_target_drifts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    database = tmp_path / "autopilot.sqlite3"
    source_job_id, _revision = _failed_source(database, workspace)
    target = workspace / "ozon_market_analytics" / "src" / "app.py"
    with VerificationRepairStore(database, workspace=workspace) as store:
        proposal = store.proposals(source_job_id)[0]
        store.reserve(
            proposal,
            idempotency_key="repair-stale-evidence",
            repair_task_id="1" * 32,
            repair_job_id="2" * 32,
            confirmed_by="test-user",
        )
        target.write_text("value = 2\n", encoding="utf-8")
        with pytest.raises(RepairConflictError, match="STALE_EVIDENCE"):
            store.assert_write("2" * 32, proposal.allowed_paths[0])
        relation = store.relation_for_repair("2" * 32)

    assert relation is not None
    assert relation["status"] == "stale_evidence"


def test_repair_evidence_is_redacted_before_persistence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    database = tmp_path / "autopilot.sqlite3"
    secret = "REPAIR_SECRET_91827"
    source_job_id, _revision = _failed_source(database, workspace)
    with (
        AutopilotStore(database) as store,
        store._lock,
        store._connection,
    ):  # trusted test fixture mutation
        store._connection.execute(
            "UPDATE autopilot_jobs SET verification_results=? WHERE id=?",
            (
                '[{"check":"ruff_check","status":"failed",'
                '"output":"src/app.py:1:6: E225 REPAIR_SECRET_91827"}]',
                source_job_id,
            ),
        )
    with VerificationRepairStore(
        database,
        workspace=workspace,
        known_secrets=(secret,),
    ) as store:
        serialized = str(store.proposals(source_job_id)[0].public())
    assert secret not in serialized
