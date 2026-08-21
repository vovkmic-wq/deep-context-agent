"""Tests for filesystem boundary enforcement."""

from pathlib import Path

import pytest

from context_agent.errors import PathSecurityError
from context_agent.paths import resolve_inside, strip_workspace_prefix


def test_workspace_prefix_is_normalized() -> None:
    assert strip_workspace_prefix("/workspace/docs/a.txt") == Path("docs/a.txt")
    assert strip_workspace_prefix("/workspace") == Path(".")


def test_relative_path_resolves_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert resolve_inside(root, "a/b.txt") == (root / "a/b.txt").resolve()


def test_parent_traversal_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PathSecurityError, match="outside"):
        resolve_inside(root, "../secret.txt")


def test_root_modification_can_be_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PathSecurityError, match="root itself"):
        resolve_inside(root, ".", allow_root=False)


def test_symlink_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is not available")
    with pytest.raises(PathSecurityError, match="outside"):
        resolve_inside(root, "link/file.txt")
