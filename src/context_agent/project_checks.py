"""Safe, fixed-allowlist project validation commands."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from context_agent.artifact_policy import DEFAULT_ARTIFACT_POLICY
from context_agent.paths import PathSecurityError, resolve_inside
from context_agent.verification_context import (
    context_metadata,
    probe_environment,
    source_fingerprint,
)

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


class ProjectEnvironmentError(ValueError):
    """The project's interpreter is unavailable; this is not a code defect."""

    error_code = "verification_environment_unavailable"


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
    max_entries: int = 10_000,
    timeout_seconds: float = 5,
) -> Path:
    """Resolve the nearest trusted project root, then a unique bounded candidate."""

    root = workspace.resolve()
    seeded_roots: list[Path] = []
    for raw_seed in seed_paths:
        try:
            seed = resolve_inside(root, raw_seed)
        except PathSecurityError as exc:
            raise ProjectRootResolutionError("Unsafe project seed path") from exc
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
    pending = [root]
    visited: set[Path] = set()
    scanned = 0
    started = time.monotonic()
    while pending:
        directory = pending.pop()
        resolved = directory.resolve()
        if not resolved.is_relative_to(root) or resolved in visited:
            continue
        visited.add(resolved)
        with os.scandir(directory) as entries:
            for entry in entries:
                scanned += 1
                if (
                    scanned > max_entries
                    or time.monotonic() - started > timeout_seconds
                ):
                    raise ProjectRootResolutionError(
                        "Project-root scan budget exceeded"
                    )
                if entry.is_symlink():
                    continue
                candidate = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if DEFAULT_ARTIFACT_POLICY.directory_reason(entry.name) is None:
                        pending.append(candidate)
                elif entry.name == _PROJECT_MANIFEST and entry.is_file():
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
    context: dict[str, Any] = field(default_factory=dict)
    output_sha256: str = ""


class ProjectCheckRunner:
    """Run selected project checks without accepting arbitrary shell input."""

    def __init__(
        self,
        *,
        workspace: Path,
        timeout_seconds: int,
        output_max_chars: int,
        environment_python: Path | None = None,
        environment_root: Path | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.timeout_seconds = timeout_seconds
        self.output_max_chars = output_max_chars
        if (environment_python is None) != (environment_root is None):
            raise ValueError(
                "Explicit environment requires both Python and project root"
            )
        self.environment_python = environment_python
        self.environment_root = environment_root.resolve() if environment_root else None

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

        requested = _parse_checks(checks)
        execution_root = self._validated_project_root(project_root)
        try:
            python = (
                str(self.environment_python.absolute())
                if self.environment_python and self.environment_root == execution_root
                else self._project_python(execution_root)
            )
        except ProjectEnvironmentError as exc:
            return [
                ProjectCheckResult(check, (), None, 0, "unavailable", str(exc))
                for check in requested
            ]
        environment, secret_values = _sanitized_environment()
        try:
            probe = probe_environment(
                python, execution_root, environment, self.timeout_seconds
            )
            if self.environment_root != execution_root:
                expected_prefix = Path(python).parent.parent.resolve()
                if Path(probe["prefix"]).resolve() != expected_prefix:
                    raise ValueError("Project environment identity mismatch")
            needed = {
                "ruff_check": "ruff",
                "ruff_format_check": "ruff",
                "pytest": "pytest",
                "mypy": "mypy",
            }
            missing = sorted(
                {
                    needed[c]
                    for c in requested
                    if c in needed and not probe["tools"].get(needed[c])
                }
            )
            if missing:
                raise ValueError(
                    "Project environment dependencies missing: " + ", ".join(missing)
                )
            baseline = source_fingerprint(execution_root)
            context = context_metadata(
                execution_root, self.workspace, python, probe, requested, baseline
            )
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else ""
            if not reason.startswith(("Project environment", "Verification source")):
                reason = "Project environment preflight failed: " + type(exc).__name__
            return [
                ProjectCheckResult(
                    check,
                    (),
                    None,
                    0,
                    "unavailable",
                    reason,
                )
                for check in requested
            ]
        environment.pop("VIRTUAL_ENV", None)
        if probe["prefix"] != probe["base_prefix"]:
            environment["VIRTUAL_ENV"] = probe["prefix"]
        environment["PATH"] = (
            str(Path(python).parent) + os.pathsep + environment.get("PATH", "")
        )
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
                            context=context,
                            output_sha256=hashlib.sha256(
                                output.encode("utf-8")
                            ).hexdigest(),
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
                            context=context,
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
                        context=context,
                        output_sha256=hashlib.sha256(
                            output.encode("utf-8")
                        ).hexdigest(),
                    )
                )

        try:
            fresh = source_fingerprint(execution_root) == baseline
        except (ValueError, OSError):
            fresh = False
        if not fresh:
            results = [
                replace(
                    result,
                    status="stale",
                    output=(
                        "Verification context changed during checks; "
                        "results are not current.\n" + result.output
                    ),
                )
                for result in results
            ]
        return results

    def _validated_project_root(self, project_root: Path | None) -> Path:
        resolved = (
            project_root.resolve()
            if project_root is not None
            else resolve_project_root(self.workspace)
        )
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
                return str(candidate.absolute())
        raise ProjectEnvironmentError(
            "Project environment unavailable: configure the project's virtual "
            "environment before running checks. Agent Python fallback is disabled."
        )

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
