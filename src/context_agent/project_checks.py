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

from context_agent.paths import PathSecurityError, resolve_inside

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
_PROJECT_MANIFEST: Final[str] = "pyproject.toml"
_ROOT_SCAN_EXCLUDES: Final[frozenset[str]] = frozenset(
    {
        ".agent_data",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)


class ProjectRootResolutionError(ValueError):
    """Raised when a safe, unambiguous project root cannot be resolved."""

    error_code = "verification_project_root_unresolved"


class AmbiguousProjectRootError(ProjectRootResolutionError):
    """Raised when multiple equally plausible project roots exist."""

    error_code = "verification_project_root_ambiguous"


def resolve_project_root(
    workspace: Path,
    *,
    seed_paths: tuple[str, ...] = (),
    max_manifests: int = 100,
) -> Path:
    """Resolve the nearest trusted project root, then a unique bounded candidate."""

    root = workspace.resolve()
    seeded_roots: list[Path] = []
    for raw_seed in seed_paths:
        try:
            seed = resolve_inside(root, raw_seed)
        except PathSecurityError:
            continue
        current = seed if seed.is_dir() else seed.parent
        while current == root or current.is_relative_to(root):
            if (current / _PROJECT_MANIFEST).is_file():
                seeded_roots.append(current)
                break
            if current == root:
                break
            current = current.parent
    unique_seeded = tuple(dict.fromkeys(seeded_roots))
    if len(unique_seeded) == 1:
        return unique_seeded[0]
    if len(unique_seeded) > 1:
        raise AmbiguousProjectRootError(
            "Seed paths resolve to multiple projects: "
            + ", ".join(str(path) for path in unique_seeded)
        )

    manifests: list[Path] = []
    if (root / _PROJECT_MANIFEST).is_file():
        manifests.append(root / _PROJECT_MANIFEST)
    for candidate in root.rglob(_PROJECT_MANIFEST):
        relative_parts = candidate.relative_to(root).parts[:-1]
        if any(part.casefold() in _ROOT_SCAN_EXCLUDES for part in relative_parts):
            continue
        if candidate not in manifests:
            manifests.append(candidate)
        if len(manifests) > max_manifests:
            raise AmbiguousProjectRootError(
                "Project-root scan exceeded the bounded manifest limit."
            )
    safe_roots = tuple(
        path
        for path in dict.fromkeys(manifest.parent.resolve() for manifest in manifests)
        if path == root or path.is_relative_to(root)
    )
    if len(safe_roots) == 1:
        return safe_roots[0]
    if manifests and not safe_roots:
        raise ProjectRootResolutionError(
            "Discovered project manifests resolve outside the workspace."
        )
    if not manifests:
        raise ProjectRootResolutionError(
            f"No {_PROJECT_MANIFEST} was found inside the workspace."
        )
    raise AmbiguousProjectRootError(
        "Multiple project roots were found; name the target project explicitly: "
        + ", ".join(str(path) for path in safe_roots[:10])
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

    def run(
        self,
        checks: str = "",
        *,
        project_root: Path | None = None,
    ) -> list[ProjectCheckResult]:
        """Run comma-separated check identifiers from the immutable allowlist."""

        execution_root = self._validated_project_root(project_root)
        requested = _parse_checks(checks)
        python = self._project_python(execution_root)
        environment, secret_values = _sanitized_environment()
        results: list[ProjectCheckResult] = []

        with tempfile.TemporaryDirectory(prefix="deep-context-checks-") as temp_root:
            for check in requested:
                command = self._command(
                    check,
                    python,
                    Path(temp_root),
                    execution_root,
                )
                started = time.monotonic()
                try:
                    completed = subprocess.run(
                        command,
                        cwd=execution_root,
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

    def _validated_project_root(self, project_root: Path | None) -> Path:
        resolved = (project_root or self.workspace).resolve()
        if resolved != self.workspace and not resolved.is_relative_to(self.workspace):
            raise ValueError("Project-check root escapes workspace")
        if not resolved.is_dir():
            raise ValueError("Project-check root is not a directory")
        return resolved

    @staticmethod
    def _project_python(project_root: Path) -> str:
        candidates = (
            project_root / ".venv" / "Scripts" / "python.exe",
            project_root / ".venv" / "bin" / "python",
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
        return sys.executable

    def _command(
        self,
        check: str,
        python: str,
        temp_root: Path,
        project_root: Path,
    ) -> list[str]:
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
                if (project_root / target).exists()
            ]
            return [python, "-m", "mypy", *(targets or ["."])]
        if check == "compileall":
            targets = [
                target
                for target in ("src", "tests", "main.py")
                if (project_root / target).exists()
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
