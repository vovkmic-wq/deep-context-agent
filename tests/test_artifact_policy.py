"""Tests for unified exclusions and bounded workspace traversal."""

from pathlib import Path

import pytest

from context_agent.artifact_policy import scan_workspace_page
from context_agent.project_audit import select_project_files


def test_policy_is_shared_and_pages_never_return_generated_artifacts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(5):
        (workspace / f"source-{index}.py").write_text("print('ok')\n", encoding="utf-8")
    for relative in (".git/config", ".venv/package.py", "node_modules/pkg.js"):
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("excluded", encoding="utf-8")

    first = scan_workspace_page(workspace, page_size=2, text_only=True)
    second = scan_workspace_page(
        workspace,
        page_size=2,
        cursor=first.next_cursor or "",
        text_only=True,
    )
    third = scan_workspace_page(
        workspace,
        page_size=2,
        cursor=second.next_cursor or "",
        text_only=True,
    )
    paged = {path.name for page in (first, second, third) for path in page.paths}
    audited = {path.name for path in select_project_files(workspace).paths}

    assert paged == audited == {f"source-{index}.py" for index in range(5)}
    assert not first.complete and not second.complete and third.complete
    assert first.next_cursor != second.next_cursor


def test_cursor_is_bound_to_root_filter_and_checksum(tmp_path: Path) -> None:
    first_root = tmp_path / "one"
    second_root = tmp_path / "two"
    first_root.mkdir()
    second_root.mkdir()
    for root in (first_root, second_root):
        (root / "a.py").write_text("a", encoding="utf-8")
        (root / "b.py").write_text("b", encoding="utf-8")

    first = scan_workspace_page(first_root, pattern="*.py", page_size=1)
    assert first.next_cursor
    with pytest.raises(ValueError, match="Invalid traversal cursor"):
        scan_workspace_page(
            second_root,
            pattern="*.py",
            cursor=first.next_cursor or "",
            page_size=1,
        )
    with pytest.raises(ValueError, match="Invalid traversal cursor"):
        scan_workspace_page(
            first_root,
            pattern="*.txt",
            cursor=first.next_cursor or "",
            page_size=1,
        )
    tampered = (first.next_cursor or "")[:-1] + "A"
    with pytest.raises(ValueError, match="Invalid traversal cursor"):
        scan_workspace_page(
            first_root,
            pattern="*.py",
            cursor=tampered,
            page_size=1,
        )
