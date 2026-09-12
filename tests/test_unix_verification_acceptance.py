"""A23: actual POSIX symlink launch and confinement, never a mocked platform."""

import os
import subprocess
import venv
from pathlib import Path

import pytest

from context_agent.paths import PathSecurityError, resolve_inside
from context_agent.project_checks import ProjectCheckRunner, resolve_project_root
from context_agent.verification_context import (
    VerificationContext,
    VerificationContextStore,
)

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="Requires real POSIX symlinks"
)


def test_symlink_python_keeps_project_prefix_and_persisted_launch(tmp_path):
    project = tmp_path / "nested project \u041e\u0437\u043e\u043d"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    package = project / "src" / "fixture_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n")
    environment = project / ".venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    launch = environment / "bin" / "python"
    assert launch.is_symlink(), "A23 must exercise a real symlink, not a copied binary"
    assert not launch.resolve().is_relative_to(environment)
    site = subprocess.run(
        [str(launch), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    ).stdout.strip()
    (Path(site) / "fixture.pth").write_text(
        str(project / "src") + "\n", encoding="utf-8"
    )
    database = tmp_path / "context.sqlite3"
    runner = ProjectCheckRunner(
        workspace=tmp_path,
        context_database=database,
        timeout_seconds=20,
        output_max_chars=2000,
    )
    result = runner.run("compileall", project_root=project)[0]
    assert result.status == "passed", result.output
    assert result.command[0] == str(launch)
    identity = result.context["persisted_context_id"]
    with VerificationContextStore(database) as store:
        context = VerificationContext.from_payload(store.load(tmp_path, identity))
    assert context.launch_reference == str(launch)
    assert Path(context.prefix) == environment
    assert context.prefix != context.base_prefix
    assert dict(context.package_origins)["fixture_pkg"] == (
        str(package / "__init__.py"),
    )
    restarted = ProjectCheckRunner(
        workspace=tmp_path,
        context_database=database,
        timeout_seconds=20,
        output_max_chars=2000,
    )
    assert restarted.restore_project_root(identity) == project


def test_manifest_discovery_ignores_external_symlink_project(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "private"
    inside = workspace / "project"
    inside.mkdir(parents=True)
    outside.mkdir()
    for root in (inside, outside):
        (root / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    (workspace / "external").symlink_to(outside, target_is_directory=True)
    assert resolve_project_root(workspace) == inside
    with pytest.raises(PathSecurityError):
        resolve_inside(workspace, "external/pyproject.toml")
    with pytest.raises(ValueError, match="Unsafe"):
        resolve_project_root(workspace, seed_paths=("/workspace/external",))
