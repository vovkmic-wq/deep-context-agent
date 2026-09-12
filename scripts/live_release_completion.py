"""Opt-in real-provider FAIL/approved REPAIR/PASS on isolated nested projects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import venv
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from context_agent.autopilot import AutopilotStore
from context_agent.config import AppConfig, ProviderConfig
from context_agent.project_checks import ProjectCheckResult, verification_passed
from context_agent.task_state import TaskStateStore
from context_agent.verification_context import (
    VerificationContext,
    VerificationContextStore,
)
from context_agent.web import create_app


def create_fixture(run: Path) -> Path:
    project = run / "workspace" / "ozon_like"
    package = project / "src" / "fixture_calc"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_calc.py").write_text(
        "from fixture_calc.calc import add\n\n\n"
        "def test_add() -> None:\n    assert add(5, 2) == 7\n",
        encoding="utf-8",
    )
    (project / "pyproject.toml").write_text(
        "[project]\nname='fixture-calc'\nversion='0.0.1'\n"
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n"
        "[tool.mypy]\nfiles=['src']\nstrict=true\n",
        encoding="utf-8",
    )
    environment = project / ".venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    install = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "ruff",
            "pytest",
            "mypy",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if install.returncode:
        raise RuntimeError("isolated_fixture_dependencies_unavailable")
    probe = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    (Path(probe.stdout.strip()) / "fixture-project.pth").write_text(
        str(project / "src") + "\n",
        encoding="utf-8",
    )
    return project


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env.local", override=False)
    load_dotenv(root / ".env", override=False)
    providers = tuple(
        replace(p, timeout=60) for p in ProviderConfig.priority_from_env()
    )
    for number in range(args.runs):
        run = Path(tempfile.mkdtemp(prefix="dca-release-live-")).resolve()
        print(f"run={number + 1} evidence_directory={run}", flush=True)
        project = create_fixture(run)
        config = AppConfig(
            project_root=root,
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
        receipts = []

        def drain(client, task_id, run=run, receipts=receipts):
            stream = client.get(f"/api/events/{task_id}").text
            status = client.get(f"/api/tasks/{task_id}").json()
            (run / f"{task_id}.sse.txt").write_text(stream, encoding="utf-8")
            receipts.append(status)
            print(json.dumps({"task_id": task_id, "status": status}), flush=True)
            return status

        def submit(client, query, write, execution):
            csrf = client.get("/api/runtime").json()["csrf_token"]
            reply = client.post(
                "/api/chat",
                headers={"x-csrf-token": csrf},
                json={
                    "query": query,
                    "thread_id": "live-release",
                    "mode": "agent",
                    "allow_write": write,
                    "execution_mode": execution,
                    "auto_context": False,
                },
            )
            if reply.status_code != 202:
                raise RuntimeError(f"chat_rejected_{reply.status_code}")
            payload = reply.json()
            drain(client, payload["task_id"])
            return payload

        started = time.monotonic()
        tests_before = (project / "tests" / "test_calc.py").read_bytes()
        with TestClient(create_app(config, providers)) as client:
            submit(
                client,
                "Create /workspace/ozon_like/src/fixture_calc/calc.py containing "
                "an unused import os followed by exactly this typed function: "
                "def add(a: int, b: int) -> int: return a + b. "
                "Use normal Python indentation, TWO blank lines after the import "
                "and a trailing newline. Keep the unused import intentionally. "
                "This intentionally faulty fixture tests a repair workflow. "
                "Do not read or change other files and do not run checks.",
                True,
                "single-turn",
            )
            target = project / "src" / "fixture_calc" / "calc.py"
            assert target.is_file(), "Implementation did not create the target"
            before = hashlib.sha256(target.read_bytes()).hexdigest()
            verified = submit(
                client,
                "Run verification only for /workspace/ozon_like: Ruff check, "
                "Ruff format --check, pytest, mypy and compileall. "
                "Do not modify files.",
                False,
                "autopilot",
            )
            source_id = verified["job_id"]
            with AutopilotStore(config.autopilot_database) as store:
                original = store.details(source_id)
            assert original["verification_status"] == "failed", original["status"]
            assert hashlib.sha256(target.read_bytes()).hexdigest() == before
        # Restart the application before explicit approval; preserve all evidence.
        with TestClient(create_app(config, providers)) as client:
            csrf = client.get("/api/runtime").json()["csrf_token"]
            response = client.post(
                f"/api/jobs/{source_id}/repair-proposals",
                headers={"x-csrf-token": csrf},
            )
            assert response.status_code == 200, response.status_code
            proposals = response.json()["items"]
            proposal = next(p for p in proposals if p["allowed_paths"])
            approval = {
                "confirmed": True,
                "proposal_id": proposal["proposal_id"],
                "expected_checkpoint_revision": original["checkpoint_revision"],
                "plan_sha256": proposal["plan_sha256"],
                "evidence_snapshot_sha256": proposal["evidence_sha256"],
                "idempotency_key": "live-explicit-repair-once",
                "evidence_ids": [e["evidence_id"] for e in proposal["evidence"]],
            }
            denied = client.post(
                f"/api/jobs/{source_id}/repair-task",
                headers={"x-csrf-token": csrf},
                json={**approval, "confirmed": False},
            )
            assert denied.status_code == 409
            created = client.post(
                f"/api/jobs/{source_id}/repair-task",
                headers={"x-csrf-token": csrf},
                json=approval,
            )
            assert created.status_code == 202, created.status_code
            payload = created.json()
            drain(client, payload["task_id"])
            repeated = client.post(
                f"/api/jobs/{source_id}/repair-task",
                headers={"x-csrf-token": csrf},
                json=approval,
            ).json()
            assert repeated["repair_job_id"] == payload["repair_job_id"]
            assert repeated["reused"]
            with AutopilotStore(config.autopilot_database) as store:
                final = store.details(payload["repair_job_id"])
                unchanged = store.details(source_id)
            for field in (
                "status",
                "mode",
                "verification_results",
                "checkpoint_revision",
                "tool_receipts",
            ):
                assert unchanged[field] == original[field], field
            assert final["verification_status"] == "passed", final["last_error_code"]
            checks = final["verification_results"]
            assert verification_passed(
                [
                    ProjectCheckResult(
                        check=c["check"],
                        command=tuple(c["command"]),
                        status=c["status"],
                        return_code=c["return_code"],
                        duration_seconds=c["duration_seconds"],
                        output=c["output"],
                        context=c["verification_context"],
                    )
                    for c in checks
                ],
                (
                    "ruff_check",
                    "ruff_format_check",
                    "pytest",
                    "mypy",
                    "compileall",
                ),
            )
            assert hashlib.sha256(target.read_bytes()).hexdigest() != before
            assert (project / "tests" / "test_calc.py").read_bytes() == tests_before
            source_context_id = original["verification_results"][0][
                "verification_context"
            ]["persisted_context_id"]
            final_context_id = checks[0]["verification_context"]["persisted_context_id"]
            with VerificationContextStore(config.context_database) as store:
                source_context = VerificationContext.from_payload(
                    store.load(config.workspace, source_context_id)
                )
                final_context = VerificationContext.from_payload(
                    store.load(config.workspace, final_context_id)
                )
            assert source_context.invocation.job_id == source_id
            assert final_context.invocation.job_id == payload["repair_job_id"]
            assert source_context.invocation.task_id
            assert final_context.invocation.task_id
            assert final_context.parent_context_id == source_context_id
            assert final_context.root_source == "saved_context"
            assert final_context.invocation.checks_allowed
            assert not final_context.invocation.write_allowed
            assert final_context.source_sha256 != source_context.source_sha256
            assert final_context.dependency_versions
            assert "pyproject.toml" in dict(final_context.configuration_hashes)
            with TaskStateStore(config.context_database) as store:
                saved = [
                    t.public() for t in store.list("live-release", config.workspace)
                ]
            evidence = {
                "result": "PASS",
                "duration_seconds": round(time.monotonic() - started, 2),
                "source_job_id": source_id,
                "repair_job_id": payload["repair_job_id"],
                "context_contract": "schema2: provenance + durable parent link PASS",
                "checks": checks,
                "saved_tasks": saved,
                "web_tasks": receipts,
            }
            (run / "acceptance.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"run={number + 1} result=PASS", flush=True)


if __name__ == "__main__":
    main()
