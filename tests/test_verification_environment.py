"""Do not silently verify a nested project with the agent interpreter."""

import json
import subprocess
import venv
from pathlib import Path

import pytest

from context_agent.project_checks import ProjectCheckRunner, resolve_project_root
from context_agent.verification_context import probe_environment


def test_package_origin_in_another_checkout_is_rejected(tmp_path, monkeypatch):
    package = tmp_path / "src" / "fixturepkg"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    payload = {
        "version": "3.12",
        "prefix": "env",
        "base_prefix": "base",
        "tools": {},
        "origins": {"fixturepkg": str(tmp_path.parent / "other" / "__init__.py")},
    }
    monkeypatch.setattr(
        "context_agent.verification_context.run_checked_process",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, json.dumps(payload), ""
        ),
    )
    with pytest.raises(ValueError, match="origin"):
        probe_environment("python", tmp_path, {}, 5)


@pytest.mark.parametrize("wrong_copy", [False, True])
@pytest.mark.parametrize("layout", ["regular", "namespace", "custom"])
def test_real_venv_checks_editable_package_origin(tmp_path, wrong_copy, layout):
    project = tmp_path / "project"
    source_dir = "lib" if layout == "custom" else "src"
    package = project / source_dir / "fixturepkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n")
    (project / "pyproject.toml").write_text("[project]\nname='fixturepkg'\n")
    if layout == "namespace":
        (package / "__init__.py").unlink()
        (package / "module.py").write_text("VALUE = 1\n")
    if layout == "custom":
        (project / "pyproject.toml").write_text(
            "[project]\nname='fixturepkg'\n"
            "[tool.setuptools.packages.find]\nwhere=['lib']\n"
        )
    venv.EnvBuilder(with_pip=False).create(project / ".venv")
    python = ProjectCheckRunner._project_python(project)
    site = subprocess.run(
        [python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    import_root = project / source_dir
    if wrong_copy:
        import_root = tmp_path / "other" / "src"
        (import_root / "fixturepkg").mkdir(parents=True)
        (import_root / "fixturepkg" / "__init__.py").write_text("VALUE = 2\n")
        if layout == "namespace":
            (import_root / "fixturepkg" / "__init__.py").unlink()
            (import_root / "fixturepkg" / "module.py").write_text("VALUE = 2\n")
    (Path(site) / "fixture-origin.pth").write_text(str(import_root) + "\n")
    runner = ProjectCheckRunner(
        workspace=project, timeout_seconds=10, output_max_chars=1000
    )
    result = runner.run("compileall")[0]
    assert result.status == ("unavailable" if wrong_copy else "passed"), result.output
    if wrong_copy:
        assert "origin" in result.output


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
