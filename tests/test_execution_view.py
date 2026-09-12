"""Public execution cards exclude process identity, prompts and host paths."""

import json

from context_agent.diagnostics import DiagnosticStore
from context_agent.execution_view import execution_view, public_job_details


def test_default_details_omit_free_text_and_nested_host_metadata():
    job = {
        "id": "job",
        "objective": "PRIVATE_PROMPT",
        "lease_token": "PRIVATE_OWNER",
        "workspace": "C:/PRIVATE_PROJECT",
        "last_error_message": "PRIVATE_MESSAGE",
        "progress": {
            "status": "blocked",
            "changed_files": 1,
            "next_operation_data": {"objective": "PRIVATE_NESTED"},
        },
        "verification_results": [
            {"python": "C:/PRIVATE_PYTHON", "output": "PRIVATE_LOG"}
        ],
    }
    serialized = json.dumps(public_job_details(job, execution_view(job)))
    assert "PRIVATE_" not in serialized
    assert "C:/" not in serialized
    assert '"changed_files": 1' in serialized


def test_public_card_and_durable_replay_are_safe(tmp_path):
    context = {
        "persisted_context_id": "a" * 64,
        "project_root": "/workspace/ozon",
        "environment_label": ".venv",
        "python": "C:/secret/python.exe",
    }
    view = execution_view(
        {
            "id": "job",
            "task_identity": "saved",
            "status": "blocked",
            "phase": "blocked",
            "last_error_code": "verification_failed",
            "owner": "PRIVATE_OWNER",
            "objective": "PRIVATE_PROMPT",
            "verification_status": "failed",
            "verification_results": [
                {
                    "check": "pytest",
                    "status": "failed",
                    "return_code": 1,
                    "verification_context": context,
                    "output": "PRIVATE_OUTPUT",
                }
            ],
        },
        {"status": "running", "projection_state": "pending"},
    )
    assert view["display_status"] == "finalization_pending"
    assert view["project_root"] == "/workspace/ozon"
    with DiagnosticStore(tmp_path / "journal.sqlite3") as store:
        store.record_task_start("web", "acceptance")
        event = {"event": "blocked", "data": {"execution": view}}
        store.record_task_terminal("web", event)
        store.record_task_event("web", "blocked", {"execution": view})
    with DiagnosticStore(tmp_path / "journal.sqlite3") as store:
        serialized = json.dumps(store.task_events("web"))
        assert "PRIVATE_" not in serialized
        assert "C:/" not in serialized
        assert "finalization_pending" in serialized


def test_mixed_contexts_do_not_display_an_invented_root():
    view = execution_view(
        {
            "verification_results": [
                {
                    "check": "pytest",
                    "verification_context": {
                        "persisted_context_id": "a" * 64,
                        "project_root": "/workspace/a",
                    },
                },
                {
                    "check": "mypy",
                    "verification_context": {
                        "persisted_context_id": "b" * 64,
                        "project_root": "/workspace/b",
                    },
                },
            ]
        }
    )
    assert view["project_root"] == "unavailable"
    assert view["context_id"] == ""
