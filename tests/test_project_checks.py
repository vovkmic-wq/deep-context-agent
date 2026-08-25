"""Tests for the fixed-allowlist project check runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from context_agent.project_checks import ProjectCheckRunner


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

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = ProjectCheckRunner(
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=80,
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


def test_compileall_check_runs_with_fixed_arguments(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    runner = ProjectCheckRunner(
        workspace=tmp_path,
        timeout_seconds=30,
        output_max_chars=2_000,
    )
    result = runner.run("compileall")[0]
    assert result.status == "passed"
    assert result.return_code == 0
