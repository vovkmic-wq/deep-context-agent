"""Run fixed verification commands with cooperative cancellation and tree cleanup."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path


def _stop_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        directory = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetSystemDirectoryW(directory, len(directory)):
            subprocess.run(
                [
                    str(Path(directory.value) / "taskkill.exe"),
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
    else:
        with suppress(ProcessLookupError):
            vars(os)["killpg"](process.pid, vars(signal)["SIGKILL"])
    if process.poll() is None:
        process.kill()


def run_checked_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    check_authority: Callable[[], None],
    capture_output: bool = True,
    text: bool = True,
    encoding: str = "utf-8",
    errors: str = "replace",
    check: bool = False,
    shell: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Check authority every 100 ms; stop only the process tree created here."""
    if shell or check or not capture_output or not text:
        raise ValueError("Unsupported verification process options")
    check_authority()
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NO_WINDOW
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding=encoding,
        errors=errors,
        shell=False,
        start_new_session=os.name != "nt",
        creationflags=creation_flags,
    ) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                check_authority()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                    check_authority()
                    return subprocess.CompletedProcess(
                        command, process.returncode, stdout, stderr
                    )
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise
        except BaseException as exc:
            with suppress(OSError, subprocess.SubprocessError):
                _stop_tree(process)
            if process.poll() is None:
                process.kill()
            try:
                stdout, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                # Do not replace the original revocation by a cleanup error.
                stdout, stderr = "", ""
            if isinstance(exc, subprocess.TimeoutExpired):
                raise subprocess.TimeoutExpired(
                    command, timeout, stdout, stderr
                ) from exc
            raise
