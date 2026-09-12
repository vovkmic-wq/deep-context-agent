"""Real subprocess cancellation/timeout tests on disposable process trees."""

import os
import subprocess
import sys
import threading
import time

import pytest

from context_agent.checked_process import run_checked_process
from context_agent.verification_context import probe_environment


def test_environment_probe_is_also_cancellable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "context_agent.verification_context._PROBE", "import time; time.sleep(30)"
    )
    started = time.monotonic()

    def authority():
        if time.monotonic() - started > 0.3:
            raise RuntimeError("cancelled during preflight")

    with pytest.raises(RuntimeError, match="cancelled during preflight"):
        probe_environment(sys.executable, tmp_path, dict(os.environ), 10, authority)
    assert time.monotonic() - started < 5


def test_cleanup_error_preserves_cancellation_reason(tmp_path, monkeypatch):
    def failed_cleanup(_process):
        raise OSError("synthetic tree cleanup error")

    monkeypatch.setattr("context_agent.checked_process._stop_tree", failed_cleanup)
    started = time.monotonic()

    def authority():
        if time.monotonic() - started > 0.3:
            raise RuntimeError("original cancellation")

    with pytest.raises(RuntimeError, match="original cancellation"):
        run_checked_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            env=dict(os.environ),
            timeout=10,
            check_authority=authority,
        )
    assert time.monotonic() - started < 5


def test_cancel_stops_descendant_without_late_writes(tmp_path):
    marker = tmp_path / "late.txt"
    ready = tmp_path / "ready.txt"
    child = (
        "from pathlib import Path; import time; "
        f"Path({str(ready)!r}).write_text('ready'); "
        "time.sleep(1); "
        f"Path({str(marker)!r}).write_text('UNSAFE'); time.sleep(30)"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
    )

    def authority():
        if ready.exists():
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        run_checked_process(
            [sys.executable, "-c", parent],
            cwd=tmp_path,
            env=dict(os.environ),
            timeout=5,
            check_authority=authority,
        )
    threading.Event().wait(1.2)
    assert not marker.exists(), "Cancelled descendant performed a late mutation"


def test_cancel_stops_running_process_before_timeout(tmp_path):
    started = time.monotonic()

    def authority():
        if time.monotonic() - started > 0.5:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        run_checked_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            env=dict(os.environ),
            timeout=30,
            check_authority=authority,
        )
    assert time.monotonic() - started < 5


def test_timeout_preserves_output_and_reaps_process(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired) as error:
        run_checked_process(
            [
                sys.executable,
                "-c",
                "import time; print('READY', flush=True); time.sleep(30)",
            ],
            cwd=tmp_path,
            env=dict(os.environ),
            timeout=0.5,
            check_authority=lambda: None,
        )
    assert "READY" in error.value.output


def test_completed_command_returns_real_output(tmp_path):
    result = run_checked_process(
        [sys.executable, "-c", "print('OK')"],
        cwd=tmp_path,
        env=dict(os.environ),
        timeout=5,
        check_authority=lambda: None,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "OK"
