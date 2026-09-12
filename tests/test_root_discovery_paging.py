"""Root discovery checkpoints the frontier, rather than losing partial work."""

import sqlite3

import pytest

from context_agent.project_checks import (
    ProjectRootScanPending,
    resolve_project_root,
)


def test_page_survives_restart_and_prunes_artifacts(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for number in range(12):
        directory = workspace / f"d{number}"
        directory.mkdir()
        (directory / "note.txt").touch()
    (workspace / "d11" / "pyproject.toml").write_text("[project]\nname='real'\n")
    ignored = workspace / ".venv"
    ignored.mkdir()
    (ignored / "pyproject.toml").touch()
    database = tmp_path / "scan.db"
    cursor = None
    totals = []
    for _ in range(60):
        try:
            root = resolve_project_root(workspace, database=database, max_entries=3)
        except ProjectRootScanPending as exc:
            if cursor:
                assert exc.cursor == cursor
            cursor = exc.cursor
            totals.append(exc.scanned)
        else:
            assert root == workspace / "d11"
            break
    else:
        pytest.fail("scan failed to make bounded progress")
    assert len(totals) > 2
    assert totals == sorted(totals)


def test_changed_directory_does_not_hide_another_project(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "pyproject.toml").touch()
    (root / "file.txt").touch()
    database = tmp_path / "scan.db"
    with pytest.raises(ProjectRootScanPending):
        resolve_project_root(root, database=database, max_entries=1)
    other = root / "other"
    other.mkdir()
    (other / "pyproject.toml").touch()
    with pytest.raises(ValueError, match="changed"):
        resolve_project_root(root, database=database, max_entries=1)


def test_seed_escape_stays_denied_even_with_scan_state(tmp_path):
    with pytest.raises(ValueError, match="Unsafe"):
        resolve_project_root(
            tmp_path,
            seed_paths=("/workspace/../private",),
            database=tmp_path / "scan.db",
        )


def test_scanning_does_not_block_task_heartbeat_writer(tmp_path):
    from context_agent.workspace_scan import DurableManifestScan

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "pyproject.toml").touch()
    database = tmp_path / "scan.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE heartbeat (n INTEGER)")

    def heartbeat():
        with sqlite3.connect(database, timeout=0.05) as db:
            db.execute("INSERT INTO heartbeat VALUES (1)")

    with DurableManifestScan(database) as scanner:
        page = scanner.page(
            root, max_entries=20, timeout=1, max_matches=10, check_authority=heartbeat
        )
    assert page.complete


def test_scan_scope_isolated_between_workspaces(tmp_path):
    database = tmp_path / "scan.db"
    cursors = []
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        (root / "pyproject.toml").touch()
        (root / "file.txt").touch()
        with pytest.raises(ProjectRootScanPending) as pending:
            resolve_project_root(root, database=database, max_entries=1)
        cursors.append(pending.value.cursor)
    assert len(set(cursors)) == 2


def test_cancel_preserves_last_committed_cursor(tmp_path):
    from context_agent.reliability import ExecutionStopped
    from context_agent.workspace_scan import DurableManifestScan

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "pyproject.toml").touch()
    for index in range(10):
        (root / f"file{index}.txt").touch()
    database = tmp_path / "scan.db"
    with DurableManifestScan(database) as scan:
        first = scan.page(root, max_entries=2, timeout=1, max_matches=10)

    def cancelled():
        raise ExecutionStopped("task_cancelled")

    with DurableManifestScan(database) as scan:
        with pytest.raises(ExecutionStopped):
            scan.page(
                root,
                max_entries=2,
                timeout=1,
                max_matches=10,
                check_authority=cancelled,
            )
        next_page = scan.page(root, max_entries=2, timeout=1, max_matches=10)
    assert next_page.next_cursor == first.next_cursor
    assert next_page.scanned > first.scanned


def test_wide_tree_pages_do_not_accept_first_manifest(tmp_path):
    from context_agent.project_checks import AmbiguousProjectRootError

    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(1000):
        (root / f"file{index:04}.txt").touch()
    for name in ("one", "two", ".venv"):
        directory = root / name
        directory.mkdir()
        (directory / "pyproject.toml").touch()
    for index in range(1000):
        (root / ".venv" / f"ignored{index}.txt").touch()
    pages = 0
    with pytest.raises(AmbiguousProjectRootError):
        for _ in range(30):
            try:
                resolve_project_root(
                    root, database=tmp_path / "scan.db", max_entries=100
                )
            except ProjectRootScanPending as pending:
                pages += 1
                assert pending.scanned <= 1005  # Artifact children are never visited.
    assert pages >= 10
