"""Opt-in real Web/pytest cancellation on a fresh nested fixture, without LLM."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from live_release_completion import create_fixture

from context_agent.autopilot import AutopilotStore
from context_agent.config import AppConfig, ProviderConfig
from context_agent.task_state import TaskStateStore
from context_agent.web import create_app


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise OSError("Cannot determine fixture process status")
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise OSError("Cannot read fixture process exit code")
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="dca-web-cancel-")).resolve()
    project = create_fixture(root)
    (project / "src/fixture_calc/calc.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    marker = project / ".pytest-tmp/started"
    late = project / ".pytest-tmp/late"
    (project / "tests/test_cancel.py").write_text(
        "import os\nimport time\nfrom pathlib import Path\n\n\n"
        "def test_slow() -> None:\n"
        "    root = Path(__file__).parents[1] / '.pytest-tmp'\n"
        "    root.mkdir(exist_ok=True)\n"
        "    (root / 'started').write_text(str(os.getpid()))\n"
        "    time.sleep(30)\n"
        "    (root / 'late').touch()\n",
        encoding="utf-8",
    )
    config = AppConfig(
        project_root=root,
        workspace=root / "workspace",
        context_root=root / "workspace",
        data_dir=root / "data",
    )
    provider = ProviderConfig(
        name="lmstudio",
        model="no-model-call-allowed",
        base_url="http://127.0.0.1:9/v1",
        api_key="fixture",
    )
    print(f"cancel_run_root={root}", flush=True)
    with TestClient(create_app(config, (provider,))) as client:
        csrf = client.get("/api/runtime").json()["csrf_token"]
        headers = {"x-csrf-token": csrf}
        accepted = client.post(
            "/api/chat",
            headers=headers,
            json={
                "thread_id": "cancel-live",
                "mode": "agent",
                "allow_write": False,
                "auto_context": False,
                "execution_mode": "autopilot",
                "query": "Run verification only for /workspace/ozon_like: "
                "Ruff check, Ruff format --check, pytest, mypy and compileall. "
                "Do not modify files.",
            },
        )
        assert accepted.status_code == 202, accepted.text
        task_id = accepted.json()["task_id"]
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "pytest did not start"
        pytest_pid = int(marker.read_text())
        assert process_alive(pytest_pid)
        started = time.monotonic()
        cancelled = client.post(f"/api/chat/{task_id}/cancel", headers=headers)
        assert cancelled.status_code == 200, cancelled.text
        while time.monotonic() - started < 8:
            status = client.get(f"/api/tasks/{task_id}").json()
            if status.get("status") in {"cancelled", "failed", "blocked", "completed"}:
                break
            time.sleep(0.05)
        elapsed = time.monotonic() - started
        assert status["status"] == "cancelled", status
        assert elapsed < 8, elapsed
        events = client.get(f"/api/events/{task_id}").text
        assert "event: cancelled" in events
        assert not late.exists(), "pytest executed a late mutation"
        assert not process_alive(pytest_pid), (
            "pytest is still running after cancellation"
        )
        with AutopilotStore(config.autopilot_database) as jobs:
            job = jobs.list_jobs(workspace=config.workspace)[0]
            assert job["status"] == "cancelled", job["status"]
            assert job["verification_status"] != "passed"
        with TaskStateStore(config.context_database) as tasks:
            saved = tasks.list("cancel-live", config.workspace)[0]
            assert saved.status == "cancelled", saved.status
        report = {
            "result": "PASS",
            "elapsed_after_cancel_seconds": round(elapsed, 3),
            "web_task_id": task_id,
            "job_id": job["id"],
            "saved_task_id": saved.id,
            "late_write": False,
            "pytest_process_exited": True,
            "provider_calls": "not required: deterministic verification-only",
        }
        (root / "events.txt").write_text(events, encoding="utf-8")
        (root / "acceptance.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
