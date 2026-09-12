"""Opt-in loopback HTTP acceptance for explicit recovery and approved repair.

Creates its own temporary workspace, database, server and project environment.
Never connects to or stops an operator's running server. Existing configured
credentials are used only for the explicitly approved fixture repair.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from live_release_completion import create_fixture

from context_agent.autopilot import AutopilotStore
from context_agent.config import AppConfig, ProviderConfig
from context_agent.diagnostics import DiagnosticStore
from context_agent.project_checks import ProjectCheckResult, verification_passed
from context_agent.routing import route_chat_request
from context_agent.task_state import TaskStateStore
from context_agent.verification_context import (
    VerificationContext,
    VerificationContextStore,
)
from context_agent.web import create_app

THREAD = "live-explicit-resume"
CHECKS = ("ruff_check", "ruff_format_check", "pytest", "mypy", "compileall")


def fixture_config(run: Path) -> AppConfig:
    return AppConfig(
        project_root=Path(__file__).resolve().parents[1],
        workspace=run / "workspace",
        data_dir=run / "data",
        context_root=run / "workspace",
        model_output_tokens=4096,
        task_model_attempts=12,
        task_timeout_seconds=300,
        model_call_retries=0,
        failure_log_mode="redacted",
        autopilot_max_work_units=6,
    )


def seed_orphan(config: AppConfig) -> dict[str, Any]:
    """Only seed an isolated legacy source; production tasks are never patched."""
    with TaskStateStore(config.context_database) as store:
        source = store.create(
            THREAD,
            config.workspace,
            "Complete /workspace/ozon_like; resume final verification.",
            route_chat_request("Implement /workspace/ozon_like project"),
            False,
        )
        claimed = store.claim(source, "expired-fixture-owner", 60)
        store.save_checkpoint(claimed, {"next_step": "verify", "fixture": True})
        store.finish(
            claimed,
            "blocked",
            "reconciliation_required: fixture missing execution link",
        )
        return store.resolve(THREAD, config.workspace, source.id).public()


def snapshot_source(config: AppConfig, source_id: str) -> dict[str, Any]:
    with TaskStateStore(config.context_database) as store:
        return {
            "task": store.public_by_id(source_id, config.workspace),
            "checkpoint": store.checkpoint_by_id(source_id),
            "links": [
                dict(row)
                for row in store.db.execute(
                    "SELECT * FROM task_execution_links WHERE task_id=?", (source_id,)
                ).fetchall()
            ],
        }


def serve(run: Path, port: int) -> None:
    import uvicorn

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env.local", override=False)
    load_dotenv(root / ".env", override=False)
    providers = tuple(
        replace(provider, timeout=60) for provider in ProviderConfig.priority_from_env()
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(fixture_config(run), providers),
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="warning",
        )
    )
    stop_file = run / f"stop-{port}"

    def stop_when_requested() -> None:
        while not stop_file.exists():
            time.sleep(0.1)
        server.should_exit = True

    # The signal is private to this isolated fixture, not a production route.
    threading.Thread(target=stop_when_requested, daemon=True).start()
    server.run()


@contextmanager
def isolated_server(run: Path, generation: int):
    """Own the exact child handle; avoid arbitrary port/PID based termination."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    logs = run / f"server-{generation}.log"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with logs.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--serve",
                "--run-directory",
                str(run),
                "--port",
                str(port),
            ],
            cwd=Path(__file__).resolve().parents[1],
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=flags,
            start_new_session=sys.platform != "win32",
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False
            ) as client:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("isolated_http_server_exited")
                    try:
                        health = client.get("/api/health")
                        if health.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError("isolated_http_server_start_timeout")
                csrf = client.get("/api/runtime").json()["csrf_token"]
                client.headers["x-csrf-token"] = csrf
                yield client
        finally:
            # Let lifespan close SQLite and its workers before inspecting files.
            # Killing a venv launcher can return before its child's mapped files
            # have been released on Windows; that is not a graceful restart.
            (run / f"stop-{port}").touch()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=15)
            if process.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                        check=False,
                        creationflags=flags,
                    )
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def post(client: httpx.Client, path: str, body: dict[str, Any], code: int = 202):
    response = client.post(path, json=body)
    if response.status_code != code:
        raise AssertionError(
            f"{path}: expected HTTP {code}, got {response.status_code}: "
            f"{response.text[:1000]}"
        )
    return response.json()


def wait_result(client: httpx.Client, task_id: str, run: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        response.raise_for_status()
        status = response.json()
        if status["status"] not in {"running", "queued", "pending", "cancelling"}:
            events = client.get(f"/api/events/{task_id}", timeout=30).text
            (run / f"{task_id}.sse.txt").write_text(events, encoding="utf-8")
            print(f"web_task={task_id} status={status['status']}", flush=True)
            return status
        time.sleep(0.25)
    raise TimeoutError("isolated_web_task_deadline")


def saved_context(config: AppConfig, checks: list[dict[str, Any]]):
    context_id = checks[0]["verification_context"]["persisted_context_id"]
    with VerificationContextStore(config.context_database) as store:
        return VerificationContext.from_payload(
            store.load(config.workspace, context_id)
        )


def execute(run: Path, *, skip_repair: bool) -> None:
    started = time.monotonic()
    project = create_fixture(run)
    target = project / "src/fixture_calc/calc.py"
    target.write_text(
        "import os\n\n\ndef add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    original_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    tests_before = (project / "tests/test_calc.py").read_bytes()
    config = fixture_config(run)
    source = seed_orphan(config)
    source_id = source["task_id"]
    source_snapshot = snapshot_source(config, source_id)
    common = {"thread_id": THREAD, "expected_revision": source["revision"]}
    verification_body = {
        **common,
        "confirmed": True,
        "project_root": "/workspace/ozon_like",
        "idempotency_key": "one-linked-verification",
    }
    with isolated_server(run, 1) as client:
        rejected = post(
            client,
            f"/api/tasks/{source_id}/resume",
            {**common, "action": "continue", "idempotency_key": "unsafe-resume"},
            409,
        )
        assert "EXECUTION_LINK_UNVERIFIED" in json.dumps(rejected)
        inspection = post(client, f"/api/tasks/{source_id}/reconcile", common, 200)
        assert inspection["item"], "Missing structured reconciliation outcome"
        assert snapshot_source(config, source_id) == source_snapshot
        verification = post(
            client, f"/api/tasks/{source_id}/verification", verification_body
        )
        duplicate = post(
            client, f"/api/tasks/{source_id}/verification", verification_body
        )
        assert duplicate["task_id"] == verification["task_id"]
        assert duplicate["active_task_id"] == verification["active_task_id"]
        failed_status = wait_result(client, verification["task_id"], run)
        with AutopilotStore(config.autopilot_database) as store:
            failed_job = store.details(verification["job_id"])
        assert failed_job["workflow"] == "verification-only"
        assert failed_job["mode"] == "read-only"
        assert failed_job["verification_status"] == "failed"
        assert failed_job["last_error_code"] == "verification_failed"
        assert any(
            check["check"] == "ruff_check" and "F401" in check["output"]
            for check in failed_job["verification_results"]
        )
        verification_context = saved_context(config, failed_job["verification_results"])
        project_python = project / (
            ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
        )
        assert verification_context.project_root == "/workspace/ozon_like"
        assert Path(verification_context.launch_reference) == project_python
        assert (
            Path(verification_context.prefix).resolve() == (project / ".venv").resolve()
        )
        assert hashlib.sha256(target.read_bytes()).hexdigest() == original_hash
        assert snapshot_source(config, source_id) == source_snapshot

    with isolated_server(run, 2) as client:
        replay = post(client, f"/api/tasks/{source_id}/verification", verification_body)
        assert replay["task_id"] == verification["task_id"]
        assert replay["job_id"] == verification["job_id"]
        assert replay["reused"] is True
        assert (
            client.get(f"/api/tasks/{replay['task_id']}").json()["status"]
            == (failed_status["status"])
        )
        final: dict[str, Any] = {}
        if not skip_repair:
            proposal_reply = client.post(
                f"/api/jobs/{verification['job_id']}/repair-proposals"
            )
            assert proposal_reply.status_code == 200
            proposal = next(
                item for item in proposal_reply.json()["items"] if item["allowed_paths"]
            )
            approval = {
                "confirmed": True,
                "proposal_id": proposal["proposal_id"],
                "expected_checkpoint_revision": failed_job["checkpoint_revision"],
                "plan_sha256": proposal["plan_sha256"],
                "evidence_snapshot_sha256": proposal["evidence_sha256"],
                "idempotency_key": "one-approved-fixture-repair",
                "evidence_ids": [item["evidence_id"] for item in proposal["evidence"]],
            }
            repair_path = f"/api/jobs/{verification['job_id']}/repair-task"
            post(client, repair_path, {**approval, "confirmed": False}, 409)
            repair = post(client, repair_path, approval)
            wait_result(client, repair["task_id"], run)
            with AutopilotStore(config.autopilot_database) as store:
                final = store.details(repair["repair_job_id"])
                original = store.details(verification["job_id"])
            assert final["verification_status"] == "passed", final["last_error_code"]
            assert verification_passed(
                [
                    ProjectCheckResult(
                        check=check["check"],
                        command=tuple(check["command"]),
                        status=check["status"],
                        return_code=check["return_code"],
                        duration_seconds=check["duration_seconds"],
                        output=check["output"],
                        context=check["verification_context"],
                    )
                    for check in final["verification_results"]
                ],
                CHECKS,
            )
            repair_context = saved_context(config, final["verification_results"])
            assert repair_context.parent_context_id == verification_context.context_id
            assert repair_context.project_root == "/workspace/ozon_like"
            assert Path(repair_context.launch_reference) == project_python
            assert hashlib.sha256(target.read_bytes()).hexdigest() != original_hash
            for field in ("mode", "verification_results", "checkpoint_revision"):
                assert original[field] == failed_job[field], field
            replay_repair = post(client, repair_path, approval)
            assert replay_repair["repair_job_id"] == repair["repair_job_id"]
            assert replay_repair["reused"] is True
        assert (project / "tests/test_calc.py").read_bytes() == tests_before
        assert snapshot_source(config, source_id) == source_snapshot
        with AutopilotStore(config.autopilot_database) as store:
            all_jobs = store.list_jobs(workspace=config.workspace)
        assert len(all_jobs) == (1 if skip_repair else 2)

    with DiagnosticStore(config.diagnostics_database) as journal:
        diagnostics = journal.list_requests(limit=100)
    assert any(
        item["operation_kind"] == "task_action"
        and item["error_code"] == "execution_link_unverified"
        for item in diagnostics
    ), "Rejected explicit resume was not journaled"
    attempts = [
        {key: attempt.get(key) for key in ("provider", "model", "status")}
        for item in diagnostics
        for attempt in item.get("provider_attempts", [])
    ]
    assert skip_repair or any(item["status"] == "success" for item in attempts), (
        "No successful real-provider attempt proves the repair live test"
    )
    report = {
        "result": "PASS"
        if not skip_repair
        else "VERIFICATION_ONLY_PASS_REPAIR_NOT_RUN",
        "duration_seconds": round(time.monotonic() - started, 2),
        "transport": "real-loopback-http",
        "legacy_source_id": source_id,
        "source_unchanged": True,
        "reconciliation": inspection,
        "verification_task_id": verification["active_task_id"],
        "verification_job_id": verification["job_id"],
        "verification_context_id": verification_context.context_id,
        "project_root": verification_context.project_root,
        "environment": verification_context.environment_label,
        "same_response_after_restart": True,
        "durable_rejection_diagnostic": True,
        "provider_attempts": attempts,
        "job_count": len(all_jobs),
        "repair_job_id": final.get("id"),
        "checks": final.get("verification_results", []),
    }
    (run / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {k: v for k, v in report.items() if k not in {"checks", "reconciliation"}}
    print(json.dumps(summary), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-directory", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--skip-repair", action="store_true")
    args = parser.parse_args()
    if args.serve:
        if args.run_directory is None or not args.port:
            parser.error("isolated child requires a run directory and port")
        serve(args.run_directory, args.port)
        return
    run = Path(tempfile.mkdtemp(prefix="dca-explicit-resume-live-")).resolve()
    print(f"evidence_directory={run}", flush=True)
    execute(run, skip_repair=args.skip_repair)


if __name__ == "__main__":
    main()
