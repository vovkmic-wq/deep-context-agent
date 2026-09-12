"""Tests for the fixed-allowlist project check runner."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from context_agent.project_checks import (
    AmbiguousProjectRootError,
    ProjectCheckRunner,
    ProjectRootResolutionError,
    resolve_project_root,
)


@pytest.fixture
def runner_environment(tmp_path, monkeypatch):
    """Explicitly bind the test interpreter, never rely on production fallback."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    monkeypatch.setattr(
        "context_agent.project_checks.probe_environment",
        lambda *_: {
            "version": "fixture",
            "prefix": sys.prefix,
            "base_prefix": sys.base_prefix,
            "tools": {"ruff": True, "pytest": True, "mypy": True},
            "dependencies_sha256": "a" * 64,
        },
    )
    return {"environment_python": Path(sys.executable), "environment_root": tmp_path}


def test_project_check_runner_rejects_arbitrary_commands(tmp_path: Path) -> None:
    runner = ProjectCheckRunner(
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=2_000,
    )
    with pytest.raises(ValueError, match="Unsupported check"):
        runner.run("pytest; Remove-Item C:\\")


def test_project_check_runner_uses_no_shell_and_redacts_environment_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("OPENAI_API_KEY", "secret-api-value")
    monkeypatch.setenv("PYTHONPATH", "C:\\untrusted-injection")

    def fake_run(command: list[str], **kwargs: object):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="secret-api-value\n" + ("x" * 200),
            stderr="",
        )

    monkeypatch.setattr("context_agent.project_checks.run_checked_process", fake_run)
    runner = ProjectCheckRunner(
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=80,
        **runner_environment,
    )
    result = runner.run("ruff_check")[0]

    assert captured["shell"] is False
    assert captured["cwd"] == tmp_path.resolve()
    captured_environment = captured["env"]
    assert isinstance(captured_environment, dict)
    assert "OPENAI_API_KEY" not in captured_environment
    assert "PYTHONPATH" not in captured_environment
    assert "secret-api-value" not in result.output
    assert "[REDACTED]" in result.output
    assert "truncated" in result.output
    captured_command = captured["command"]
    assert isinstance(captured_command, list)
    assert captured_command[-4:] == ["ruff", "check", "--no-cache", "."]


def test_compileall_check_runs_with_fixed_arguments(
    tmp_path: Path, runner_environment
) -> None:
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    runner = ProjectCheckRunner(
        **runner_environment,
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=2_000,
    )
    result = runner.run("compileall")[0]
    assert result.status == "passed"
    assert result.return_code == 0


def test_resolve_project_root_uses_nearest_seed_manifest(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='outer'\n")
    nested = tmp_path / "packages" / "ozon"
    nested.mkdir(parents=True)
    (nested / "pyproject.toml").write_text("[project]\nname='ozon'\n")
    source = nested / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n")

    resolved = resolve_project_root(
        tmp_path,
        seed_paths=("/workspace/packages/ozon/src/app.py",),
    )

    assert resolved == nested.resolve()


def test_resolve_project_root_rejects_ambiguous_workspace(tmp_path: Path) -> None:
    for name in ("one", "two"):
        project = tmp_path / name
        project.mkdir()
        (project / "pyproject.toml").write_text(f"[project]\nname='{name}'\n")

    with pytest.raises(AmbiguousProjectRootError):
        resolve_project_root(tmp_path)


def test_resolve_project_root_requires_manifest(tmp_path: Path) -> None:
    with pytest.raises(ProjectRootResolutionError):
        resolve_project_root(tmp_path)


def test_runner_executes_from_explicit_nested_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "pyproject.toml").write_text("[project]\nname='nested'\n")
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("context_agent.project_checks.run_checked_process", fake_run)
    runner = ProjectCheckRunner(
        environment_python=Path(sys.executable),
        environment_root=nested,
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=2_000,
    )

    result = runner.run("ruff_check", project_root=nested)[0]

    assert result.status == "passed"
    assert captured["cwd"] == nested.resolve()


def test_runner_rejects_project_root_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-project"
    outside.mkdir(exist_ok=True)
    runner = ProjectCheckRunner(
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=2_000,
    )

    with pytest.raises(ValueError, match="escapes workspace"):
        runner.run("ruff_check", project_root=outside)


def test_runner_records_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner_environment
) -> None:
    monkeypatch.setattr(
        "context_agent.project_checks.run_checked_process",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout="failure evidence",
            stderr="",
        ),
    )
    runner = ProjectCheckRunner(
        **runner_environment,
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=2_000,
    )

    result = runner.run("ruff_check")[0]

    assert result.status == "failed"
    assert result.return_code == 1
    assert result.output == "failure evidence"


def test_runner_records_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner_environment
) -> None:
    def timeout(command: list[str], **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(command, 30, output="partial")

    monkeypatch.setattr("context_agent.project_checks.run_checked_process", timeout)
    runner = ProjectCheckRunner(
        **runner_environment,
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=2_000,
    )

    result = runner.run("ruff_check")[0]

    assert result.status == "timeout"
    assert result.return_code is None
    assert "partial" in result.output


def test_runner_records_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner_environment
) -> None:
    def fail_to_start(_command: list[str], **_kwargs: object) -> None:
        raise OSError("executable unavailable")

    monkeypatch.setattr(
        "context_agent.project_checks.run_checked_process", fail_to_start
    )
    runner = ProjectCheckRunner(
        **runner_environment,
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=2_000,
    )

    result = runner.run("ruff_check")[0]

    assert result.status == "error"
    assert result.return_code is None


def test_source_drift_invalidates_pass(tmp_path, monkeypatch, runner_environment):
    source = tmp_path / "main.py"
    source.write_text("VALUE = 1\n")

    def mutate(command, **kwargs):
        source.write_text("VALUE = 2\n")
        return subprocess.CompletedProcess(command, 0, stdout="passed", stderr="")

    monkeypatch.setattr("context_agent.project_checks.run_checked_process", mutate)
    runner = ProjectCheckRunner(
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=2000,
        **runner_environment,
    )
    result = runner.run("compileall")[0]
    assert result.status == "stale"
    assert result.context["project_root"] == "/workspace"
    assert result.context["context_id"]
    assert str(tmp_path) not in str(result.context)
    assert result.output_sha256


def test_environment_drift_invalidates_pass(tmp_path, monkeypatch, runner_environment):
    probes = iter(
        [
            {
                "version": "3.12",
                "prefix": "same",
                "base_prefix": "same",
                "tools": {},
                "dependencies_sha256": "a" * 64,
            },
            {
                "version": "3.13",
                "prefix": "same",
                "base_prefix": "same",
                "tools": {},
                "dependencies_sha256": "b" * 64,
            },
        ]
    )
    monkeypatch.setattr(
        "context_agent.project_checks.probe_environment", lambda *_: next(probes)
    )
    monkeypatch.setattr(
        "context_agent.project_checks.run_checked_process",
        lambda command, **_: subprocess.CompletedProcess(
            command, 0, stdout="", stderr=""
        ),
    )
    runner = ProjectCheckRunner(
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=2000,
        **runner_environment,
    )
    assert runner.run("compileall")[0].status == "stale"


def test_cancel_after_check_prevents_next_command(
    tmp_path, monkeypatch, runner_environment
):
    cancelled = False
    commands = []

    def check_authority():
        if cancelled:
            raise RuntimeError("verification cancelled")

    def run(command, **kwargs):
        nonlocal cancelled
        commands.append(command)
        cancelled = True
        return subprocess.CompletedProcess(command, 0, stdout="passed", stderr="")

    monkeypatch.setattr("context_agent.project_checks.run_checked_process", run)
    runner = ProjectCheckRunner(
        workspace=tmp_path,
        timeout_seconds=10,
        output_max_chars=1000,
        check_authority=check_authority,
        **runner_environment,
    )
    with pytest.raises(RuntimeError, match="cancelled"):
        runner.run("ruff_check,pytest")
    assert len(commands) == 1
