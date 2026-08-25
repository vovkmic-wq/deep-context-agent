"""Safe, fixed-allowlist project validation commands."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_SENSITIVE_ENV_MARKERS: Final[tuple[str, ...]] = (
    "ACCESS_KEY",
    "API_KEY",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)
_UNSAFE_PYTHON_ENV: Final[frozenset[str]] = frozenset(
    {"PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"}
)
_DEFAULT_CHECKS: Final[tuple[str, ...]] = (
    "ruff_check",
    "ruff_format_check",
    "pytest",
)
_ALLOWED_CHECKS: Final[frozenset[str]] = frozenset(
    {*_DEFAULT_CHECKS, "mypy", "compileall"}
)
_SECRET_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:sk-(?:proj-)?|zai[-_]?)[A-Za-z0-9._-]{12,}"
)


@dataclass(frozen=True, slots=True)
class ProjectCheckResult:
    """A bounded, serializable result from one fixed validation command."""

    check: str
    command: tuple[str, ...]
    return_code: int | None
    duration_seconds: float
    status: str
    output: str


class ProjectCheckRunner:
    """Run selected project checks without accepting arbitrary shell input."""

    def __init__(
        self,
        *,
        workspace: Path,
        timeout_seconds: int,
        output_max_chars: int,
    ) -> None:
        self.workspace = workspace.resolve()
        self.timeout_seconds = timeout_seconds
        self.output_max_chars = output_max_chars

    @property
    def allowed_checks(self) -> tuple[str, ...]:
        return tuple(sorted(_ALLOWED_CHECKS))

    def run(self, checks: str = "") -> list[ProjectCheckResult]:
        """Run comma-separated check identifiers from the immutable allowlist."""

        requested = _parse_checks(checks)
        python = self._project_python()
        environment, secret_values = _sanitized_environment()
        results: list[ProjectCheckResult] = []

        with tempfile.TemporaryDirectory(prefix="deep-context-checks-") as temp_root:
            for check in requested:
                command = self._command(check, python, Path(temp_root))
                started = time.monotonic()
                try:
                    completed = subprocess.run(
                        command,
                        cwd=self.workspace,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=self.timeout_seconds,
                        check=False,
                        shell=False,
                    )
                except subprocess.TimeoutExpired as error:
                    output = _combine_timeout_output(error)
                    results.append(
                        ProjectCheckResult(
                            check=check,
                            command=tuple(command),
                            return_code=None,
                            duration_seconds=time.monotonic() - started,
                            status="timeout",
                            output=self._sanitize_output(output, secret_values),
                        )
                    )
                    continue
                except OSError as error:
                    results.append(
                        ProjectCheckResult(
                            check=check,
                            command=tuple(command),
                            return_code=None,
                            duration_seconds=time.monotonic() - started,
                            status="error",
                            output=self._sanitize_output(str(error), secret_values),
                        )
                    )
                    continue

                output = "\n".join(
                    part.strip()
                    for part in (completed.stdout, completed.stderr)
                    if part.strip()
                )
                results.append(
                    ProjectCheckResult(
                        check=check,
                        command=tuple(command),
                        return_code=completed.returncode,
                        duration_seconds=time.monotonic() - started,
                        status="passed" if completed.returncode == 0 else "failed",
                        output=self._sanitize_output(output, secret_values),
                    )
                )

        return results

    def _project_python(self) -> str:
        candidates = (
            self.workspace / ".venv" / "Scripts" / "python.exe",
            self.workspace / ".venv" / "bin" / "python",
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
        return sys.executable

    def _command(self, check: str, python: str, temp_root: Path) -> list[str]:
        if check == "ruff_check":
            return [python, "-m", "ruff", "check", "--no-cache", "."]
        if check == "ruff_format_check":
            return [python, "-m", "ruff", "format", "--check", "--no-cache", "."]
        if check == "pytest":
            return [
                python,
                "-m",
                "pytest",
                "-ra",
                "-p",
                "no:cacheprovider",
                f"--basetemp={temp_root / 'pytest'}",
            ]
        if check == "mypy":
            targets = [
                target
                for target in ("src", "tests")
                if (self.workspace / target).exists()
            ]
            return [python, "-m", "mypy", *(targets or ["."])]
        if check == "compileall":
            targets = [
                target
                for target in ("src", "tests", "main.py")
                if (self.workspace / target).exists()
            ]
            return [python, "-m", "compileall", "-q", *(targets or ["."])]
        raise ValueError(f"Unsupported project check: {check}")

    def _sanitize_output(self, output: str, secret_values: tuple[str, ...]) -> str:
        sanitized = output
        for secret in secret_values:
            sanitized = sanitized.replace(secret, "[REDACTED]")
        sanitized = _SECRET_PATTERN.sub("[REDACTED]", sanitized)
        if len(sanitized) <= self.output_max_chars:
            return sanitized
        omitted = len(sanitized) - self.output_max_chars
        prefix = sanitized[: self.output_max_chars]
        return f"{prefix}\n...[truncated {omitted} characters]"


def _parse_checks(checks: str) -> tuple[str, ...]:
    if not checks.strip():
        return _DEFAULT_CHECKS
    requested = tuple(
        dict.fromkeys(item.strip() for item in checks.split(",") if item.strip())
    )
    unsupported = sorted(set(requested) - _ALLOWED_CHECKS)
    if unsupported:
        allowed = ", ".join(sorted(_ALLOWED_CHECKS))
        raise ValueError(
            f"Unsupported check(s): {', '.join(unsupported)}. "
            f"Allowed checks: {allowed}."
        )
    if not requested:
        raise ValueError("At least one project check is required.")
    return requested


def _sanitized_environment() -> tuple[dict[str, str], tuple[str, ...]]:
    environment: dict[str, str] = {}
    secret_values: list[str] = []
    for key, value in os.environ.items():
        normalized_key = key.upper()
        sensitive = any(
            marker in normalized_key for marker in _SENSITIVE_ENV_MARKERS
        ) or normalized_key.endswith(("_KEY", "_PAT"))
        if sensitive:
            if value:
                secret_values.append(value)
            continue
        if normalized_key in _UNSAFE_PYTHON_ENV:
            continue
        environment[key] = value
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment, tuple(secret_values)


def _combine_timeout_output(error: subprocess.TimeoutExpired) -> str:
    stdout = (
        error.stdout.decode("utf-8", errors="replace")
        if isinstance(error.stdout, bytes)
        else error.stdout
    )
    stderr = (
        error.stderr.decode("utf-8", errors="replace")
        if isinstance(error.stderr, bytes)
        else error.stderr
    )
    return "\n".join(
        part.strip() for part in (stdout or "", stderr or "") if part.strip()
    )
