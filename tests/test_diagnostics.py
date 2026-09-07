"""Durable failure-journal, privacy, retention, and restart regressions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from context_agent.diagnostics import (
    DiagnosticStore,
    classify_failure,
    redact_sensitive_text,
    safe_exception_chain,
)


def _store(
    tmp_path: Path,
    *,
    mode: str = "redacted",
    query_max_bytes: int = 65_536,
    max_rows: int = 10_000,
) -> DiagnosticStore:
    return DiagnosticStore(
        tmp_path / "diagnostics.sqlite3",
        mode=mode,  # type: ignore[arg-type]
        retention_days=30,
        max_rows=max_rows,
        query_max_bytes=query_max_bytes,
        known_secrets=("KNOWN_SECRET_58302",),
    )


def _start(store: DiagnosticStore, query: str, *, request_id: str | None = None):
    return store.start_request(
        query=query,
        thread_id="failure-thread",
        operation_kind="ask",
        source="web",
        app_version="test",
        provider_priority=[{"provider": "zhipu", "model": "glm-test"}],
        baseline_checkpoint_id="checkpoint-before",
        request_id=request_id,
    )


def test_running_model_attempt_recovers_without_fabricated_usage(tmp_path):
    with _store(tmp_path) as store:
        request_id = _start(store, "crash during model call")
        store.record_model_attempt(
            request_id,
            {
                "attempt_id": "a1",
                "provider": "lmstudio",
                "model": "model/name",
                "status": "running",
                "ordinal": 1,
            },
        )
    with _store(tmp_path) as store:
        assert store.recover_interrupted() == (1, 0)
        attempt = store.model_attempts(request_id)[0]
        assert attempt["status"] == "interrupted"
        assert attempt["input_tokens"] is None
        assert attempt["duration_ms"] is None
        assert attempt["finished_at_utc"] is not None
        assert attempt["model"] == "model/name"
        assert store.recover_interrupted() == (0, 0)


def test_redaction_covers_headers_assignments_provider_keys_and_known_values() -> None:
    provider_key = "sk-" + "proj-" + "abcdefghijklmnop"
    text = (
        "Authorization: Bearer bearer-secret-123\n"
        f"OPENAI_API_KEY={provider_key}\n"
        "Cookie: session=COOKIE_VALUE\n"
        "https://example.test/v1?api_key=URL_SECRET_VALUE\n"
        "known=KNOWN_SECRET_58302\n"
        "DO_NOT_SHOW=ATOMIC_VALUE"
    )

    redacted = redact_sensitive_text(
        text,
        known_secrets=("KNOWN_SECRET_58302",),
    )

    assert "bearer-secret" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "COOKIE_VALUE" not in redacted
    assert "URL_SECRET_VALUE" not in redacted
    assert "KNOWN_SECRET_58302" not in redacted
    assert "ATOMIC_VALUE" not in redacted
    assert redacted.count("[REDACTED]") >= 6


@pytest.mark.parametrize("mode", ["metadata", "redacted", "full"])
def test_query_modes_are_explicit_and_hashed(tmp_path: Path, mode: str) -> None:
    query = "Inspect marker and OPENAI_API_KEY=KNOWN_SECRET_58302"
    with _store(tmp_path, mode=mode) as store:
        request_id = _start(store, query)
        assert request_id is not None
        item = store.request(request_id, include_query=True)

    assert item["query_sha256"] == hashlib.sha256(query.encode()).hexdigest()
    assert item["query_mode"] == mode
    if mode == "metadata":
        assert item["query_available"] is False
        assert item["query"] is None
        assert json.loads(str(item["query_preview"])) == {
            "characters": len(query),
            "lines": 1,
            "words": len(query.split()),
        }
    elif mode == "redacted":
        assert "KNOWN_SECRET_58302" not in str(item["query"])
        assert "[REDACTED]" in str(item["query"])
    else:
        assert item["query"] == query


def test_off_mode_does_not_create_request_rows(tmp_path: Path) -> None:
    with _store(tmp_path, mode="off") as store:
        assert _start(store, "not stored") is None
        assert store.list_requests() == []


def test_query_truncation_keeps_full_hash_and_exact_size(tmp_path: Path) -> None:
    query = "НАЧАЛО-" + ("я" * 3_000) + "-КОНЕЦ"
    with _store(tmp_path, query_max_bytes=1_024) as store:
        request_id = _start(store, query)
        assert request_id is not None
        item = store.request(request_id, include_query=True)

    assert item["query_truncated"] is True
    assert item["query_bytes"] == len(query.encode("utf-8"))
    assert item["query_sha256"] == hashlib.sha256(query.encode()).hexdigest()
    assert "[TRUNCATED]" in str(item["query"])
    assert "НАЧАЛО" in str(item["query"])
    assert "КОНЕЦ" in str(item["query"])


def test_failed_request_persists_rollback_provider_and_tool_evidence(
    tmp_path: Path,
) -> None:
    secret = "KNOWN_SECRET_58302"
    with _store(tmp_path) as store:
        request_id = _start(store, f"Fail safely {secret}")
        assert request_id is not None
        try:
            raise TimeoutError(f"provider timed out {secret}")
        except TimeoutError as exc:
            store.fail_request(
                request_id,
                exc=exc,
                provider_attempts=[
                    {
                        "ordinal": 1,
                        "provider": "zhipu",
                        "model": "glm-test",
                        "status": "error",
                        "error_type": "TimeoutError",
                        "duration_ms": 50,
                    }
                ],
                tool_audit=[
                    {
                        "name": "write_file",
                        "path": "/workspace/result.txt",
                        "status": "success",
                        "result": "File written.",
                    }
                ],
                duration_ms=123,
                rollback_attempted=True,
                rollback_success=True,
                rollback_checkpoint_rows=3,
                rollback_write_rows=4,
                filesystem_side_effects=True,
            )
        item = store.request(request_id, include_query=True)

    serialized = json.dumps(item, ensure_ascii=False)
    assert item["status"] == "failed"
    assert item["error_code"] == "provider_timeout"
    assert item["retryable"] is True
    assert item["rollback_success"] is True
    assert item["rollback_checkpoint_rows"] == 3
    assert item["rollback_write_rows"] == 4
    assert item["filesystem_side_effects"] is True
    assert item["provider_attempts"][0]["provider"] == "zhipu"
    assert item["tool_audit"][0]["name"] == "write_file"
    assert "result" not in item["tool_audit"][0]
    assert secret not in serialized


def test_storage_boundary_drops_raw_tool_results_and_physical_paths(
    tmp_path: Path,
) -> None:
    secret = "KNOWN_SECRET_58302"
    with _store(tmp_path) as store:
        request_id = _start(store, "safe boundary")
        assert request_id is not None
        store.complete_request(
            request_id,
            provider_attempts=[],
            tool_audit=[
                {
                    "name": "read_file",
                    "path": f"C:/private/{secret}.txt",
                    "status": "success",
                    "result": f"raw file body {secret}",
                    "result_count": 1,
                }
            ],
            duration_ms=1,
        )
        item = store.request(request_id)

    assert item["tool_audit"] == [
        {
            "content_sha256": None,
            "name": "read_file",
            "path": None,
            "result_count": 1,
            "status": "success",
        }
    ]
    assert secret not in json.dumps(item, ensure_ascii=False)


def test_schema_v1_is_migrated_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics.sqlite3"
    with _store(tmp_path) as store:
        assert store.schema_version == 4
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE request_attempts DROP COLUMN query_preview")
        connection.execute("DROP TABLE provider_attempt_records")
        connection.execute("PRAGMA user_version=1")

    with DiagnosticStore(path) as migrated:
        request_id = _start(migrated, "migrated")
        assert request_id is not None
        migrated.complete_request(
            request_id,
            provider_attempts=[
                {
                    "ordinal": 1,
                    "provider": "zhipu",
                    "model": "glm-test",
                    "status": "success",
                    "retry_count": 0,
                    "duration_ms": 2,
                    "outcome": "active_success",
                }
            ],
            tool_audit=[],
            duration_ms=2,
        )
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(request_attempts)"
            ).fetchall()
        }
        provider_rows = connection.execute(
            "SELECT provider, outcome FROM provider_attempt_records"
        ).fetchall()

    assert version == 4
    assert "query_preview" in columns
    assert provider_rows == [("zhipu", "active_success")]


def test_web_task_events_are_ordered_redacted_and_replayable(tmp_path: Path) -> None:
    secret = "EVENT_SECRET_72914"
    with DiagnosticStore(
        tmp_path / "diagnostics.sqlite3",
        known_secrets=(secret,),
    ) as store:
        store.record_task_start("event-task", "autopilot")
        first = store.record_task_event(
            "event-task",
            "job_progress",
            {
                "phase": "discover",
                "completed_units": 1,
                "message": secret,
            },
        )
        second = store.record_task_event(
            "event-task",
            "job_progress",
            {"phase": "implement", "changed_files": 1},
        )
        replay = store.task_events("event-task", after_sequence=first)

    assert (first, second) == (1, 2)
    assert [event["sequence"] for event in replay] == [2]
    assert replay[0]["data"]["phase"] == "implement"
    assert secret not in json.dumps(replay, ensure_ascii=False)


def test_exception_group_preserves_both_safe_error_types() -> None:
    grouped = ExceptionGroup(
        "request and rollback",
        [TimeoutError("provider"), RuntimeError("rollback")],
    )
    chain = safe_exception_chain(grouped)

    assert [item["type"] for item in chain] == [
        "ExceptionGroup",
        "TimeoutError",
        "RuntimeError",
    ]
    assert classify_failure(grouped) == "provider_chain_failed"


def test_web_terminal_event_survives_store_restart(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics.sqlite3"
    first = DiagnosticStore(path)
    first.record_task_start("task-1", "chat", request_id="request-1")
    first.record_task_terminal(
        "task-1",
        {
            "event": "failed",
            "data": {
                "error_type": "provider_timeout",
                "message": "safe",
                "provider": "zhipu",
                "model": "glm-test",
                "duration_ms": 321,
                "files_scanned": 42,
                "excluded": 7,
                "partial": True,
                "cursor_available": True,
                "partial_reason": "timeout",
                "fallback_chain": [
                    {"provider": "zhipu", "model": "glm-test"},
                    {"provider": "openai", "model": "gpt-test"},
                ],
            },
        },
    )
    first.close()

    with DiagnosticStore(path) as reopened:
        task = reopened.task("task-1")

    assert task is not None
    assert task["status"] == "failed"
    assert task["terminal_event"]["data"]["message"] == "safe"  # type: ignore[index]
    terminal = task["terminal_event"]["data"]  # type: ignore[index]
    assert terminal["duration_ms"] == 321
    assert terminal["files_scanned"] == 42
    assert terminal["excluded"] == 7
    assert terminal["partial"] is True
    assert terminal["cursor_available"] is True
    assert terminal["fallback_chain"][1] == {  # type: ignore[index]
        "provider": "openai",
        "model": "gpt-test",
    }


def test_parent_request_resolves_children_and_task_reference(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        parent = store.start_request(
            query="persistent objective",
            thread_id="parent-thread",
            operation_kind="autopilot",
            source="web",
            app_version="test",
            provider_priority=[],
            baseline_checkpoint_id=None,
            task_id="web-task-parent",
            request_id="request-parent",
        )
        child = store.start_request(
            query="bounded execution unit",
            thread_id="parent-thread",
            operation_kind="autopilot-unit",
            source="web",
            app_version="test",
            provider_priority=[],
            baseline_checkpoint_id=None,
            task_id="autopilot-job",
            request_id="request-child",
            parent_request_id=parent,
        )
        assert parent and child
        store.complete_request(
            child,
            provider_attempts=[
                {
                    "provider": "zhipu",
                    "model": "glm-test",
                    "status": "success",
                    "duration_ms": 2,
                    "outcome": "completed",
                }
            ],
            tool_audit=[
                {
                    "name": "write_file",
                    "path": "/workspace/result.py",
                    "status": "success",
                }
            ],
            duration_ms=3,
        )
        store.finish_parent(parent, status="partial", duration_ms=5)
        store.finish_parent(parent, status="partial", duration_ms=6)

        by_parent = store.resolve_request(parent)
        by_task = store.resolve_request("web-task-parent")

    assert by_parent["status"] == "partial"
    children = by_parent["children"]
    assert isinstance(children, list)
    assert len(children) == 1
    assert children[0]["request_id"] == "request-child"
    assert children[0]["parent_request_id"] == "request-parent"
    assert children[0]["task_id"] == "autopilot-job"
    assert children[0]["status"] == "completed"
    assert by_task["request_id"] == "request-parent"
    assert by_parent["aggregate"]["child_count"] == 1
    assert by_parent["aggregate"]["provider_attempt_count"] == 1
    assert by_parent["aggregate"]["tool_operation_count"] == 1
    assert by_parent["provider_attempts"][0]["provider"] == "zhipu"
    assert by_parent["tool_audit"][0]["name"] == "write_file"
    assert len(by_parent["provider_attempts"]) == 1


def test_crash_recovery_marks_in_progress_request_and_task(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        request_id = _start(store, "interrupted", request_id="request-crash")
        assert request_id == "request-crash"
        store.record_task_start("task-crash", "chat", request_id=request_id)
        recovered = store.recover_interrupted()
        request = store.request(request_id)
        task = store.task("task-crash")

    assert recovered == (1, 1)
    assert request["status"] == "interrupted"
    assert request["error_code"] == "process_restart_or_crash"
    assert task is not None
    assert task["status"] == "failed"
    assert task["terminal_event"]["data"]["error_type"] == "process_interrupted"  # type: ignore[index]


def test_concurrent_writes_keep_request_ids_isolated(tmp_path: Path) -> None:
    with _store(tmp_path) as store:

        def write(index: int) -> None:
            request_id = _start(store, f"request {index}", request_id=f"req-{index}")
            assert request_id is not None
            store.complete_request(
                request_id,
                provider_attempts=[],
                tool_audit=[],
                duration_ms=index,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(40)))
        items = store.list_requests(limit=100)

    assert len(items) == 40
    assert {item["request_id"] for item in items} == {
        f"req-{index}" for index in range(40)
    }


def test_explicit_purge_never_removes_in_progress(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        active = _start(store, "active", request_id="active")
        completed = _start(store, "completed", request_id="completed")
        assert active and completed
        store.complete_request(
            completed,
            provider_attempts=[],
            tool_audit=[],
            duration_ms=1,
        )
        assert store.purge(request_id="completed") == 1
        remaining = store.list_requests()

    assert [item["request_id"] for item in remaining] == ["active"]


def test_row_retention_is_enforced_after_completion(tmp_path: Path) -> None:
    with _store(tmp_path, max_rows=100) as store:
        for index in range(105):
            request_id = _start(store, f"row {index}", request_id=f"row-{index}")
            assert request_id is not None
            store.complete_request(
                request_id,
                provider_attempts=[],
                tool_audit=[],
                duration_ms=1,
            )
        rows = store.list_requests(limit=500)

    assert len(rows) == 100
    assert rows[0]["request_id"] == "row-104"
    assert rows[-1]["request_id"] == "row-5"


def test_age_retention_removes_only_terminal_old_rows(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics.sqlite3"
    with _store(tmp_path) as store:
        old = _start(store, "old", request_id="old")
        active = _start(store, "active", request_id="active-old")
        assert old and active
        store.complete_request(
            old,
            provider_attempts=[],
            tool_audit=[],
            duration_ms=1,
        )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE request_attempts SET created_at_utc='2000-01-01T00:00:00+00:00'"
        )

    with _store(tmp_path) as store:
        assert store.cleanup() == 1
        remaining = store.list_requests()

    assert [item["request_id"] for item in remaining] == ["active-old"]
