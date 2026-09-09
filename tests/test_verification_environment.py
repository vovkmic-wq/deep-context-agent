"""Do not silently verify a nested project with the agent interpreter."""

import venv
from pathlib import Path

import pytest

from context_agent.project_checks import ProjectCheckRunner, resolve_project_root


def test_missing_project_environment_does_not_fall_back(tmp_path):
    with pytest.raises(ValueError, match="environment"):
        ProjectCheckRunner._project_python(tmp_path)


def test_project_python_keeps_launch_path(tmp_path):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    assert ProjectCheckRunner._project_python(tmp_path) == str(python)


def test_unsafe_seed_is_not_ignored(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='outer'\n")
    with pytest.raises(ValueError):
        resolve_project_root(tmp_path, seed_paths=("/workspace/../outside",))


def test_root_scan_does_not_use_unpruned_rglob(tmp_path, monkeypatch):
    project = tmp_path / "ozon"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='ozon'\n")
    ignored = tmp_path / ".venv" / "other"
    ignored.mkdir(parents=True)
    (ignored / "pyproject.toml").write_text("[project]\nname='ignored'\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("Unpruned recursive scan")

    monkeypatch.setattr(Path, "rglob", forbidden)
    assert resolve_project_root(tmp_path) == project.resolve()


def test_actual_nested_venv_preflight_and_compileall(tmp_path):
    project = tmp_path / "ozon"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    (project / "main.py").write_text("VALUE = 1\n")
    venv.EnvBuilder(with_pip=False).create(project / ".venv")
    runner = ProjectCheckRunner(
        workspace=tmp_path, timeout_seconds=30, output_max_chars=2000
    )
    result = runner.run("compileall")[0]
    assert result.status == "passed", result.output
    assert result.context["project_root"] == "/workspace/ozon"
    assert result.context["environment_label"] == ".venv"
    assert Path(result.command[0]).parent.parent == project / ".venv"
    assert result.context["python_version"]


def test_target_selects_nearest_manifest_not_workspace(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='outer'\n")
    project = tmp_path / "ozon"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='inner'\n")
    source = project / "main.py"
    source.write_text("VALUE = 1\n")
    assert (
        resolve_project_root(tmp_path, seed_paths=("/workspace/ozon/main.py",))
        == project.resolve()
    )
