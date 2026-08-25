"""Tests for resumable project audit manifests and structural indexes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import context_agent.project_audit as project_audit_module
from context_agent.project_audit import ProjectAuditStore


def test_audit_manifest_resumes_and_reopens_only_changed_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text(
        (
            '"""Module A."""\n\n'
            "def alpha(value):\n"
            '    """Return value."""\n'
            "    return value\n"
        ),
        encoding="utf-8",
    )
    (workspace / "b.txt").write_text("BETA\n", encoding="utf-8")
    (workspace / "c.md").write_text("# Gamma\n", encoding="utf-8")
    store = ProjectAuditStore(tmp_path / "data" / "audit.sqlite3")
    try:
        progress = store.start_or_resume(
            thread_id="main",
            objective="full audit",
            workspace=workspace,
            batch_size=2,
        )
        assert (progress.total, progress.pending, progress.reviewed) == (3, 3, 0)

        first = store.next_batch(progress.run_id)
        assert first is not None
        assert first.paths == ("a.py", "b.txt")
        progress = store.complete_batch(
            first,
            processed_paths={"/workspace/a.py"},
            read_counts={"/workspace/a.py": 1},
            answer="Only a.py was actually read.",
        )
        assert (progress.reviewed, progress.pending) == (1, 2)

        second = store.next_batch(progress.run_id)
        assert second is not None
        assert second.paths == ("b.txt", "c.md")
        progress = store.complete_batch(
            second,
            processed_paths={"/workspace/b.txt", "/workspace/c.md"},
            read_counts={"/workspace/b.txt": 2, "/workspace/c.md": 1},
            answer="Both remaining files were read.",
        )
        assert progress.complete
        assert progress.reviewed == 3
        assert progress.file_reads == 4

        (workspace / "a.py").write_text(
            "def alpha():\n    return 2\n",
            encoding="utf-8",
        )
        resumed = store.start_or_resume(
            thread_id="main",
            objective="full audit",
            workspace=workspace,
            batch_size=2,
        )
        assert resumed.reviewed == 2
        assert resumed.pending == 1
        changed = store.next_batch(resumed.run_id)
        assert changed is not None
        assert changed.paths == ("a.py",)
    finally:
        store.close()


def test_ast_index_and_cached_summary_do_not_reparse_unchanged_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "service.py").write_text(
        (
            '"""Payments service."""\n\n'
            "class PaymentService:\n"
            "    def charge(self, order_id, *, retry=False):\n"
            '        """Charge one order."""\n'
            "        return order_id, retry\n"
        ),
        encoding="utf-8",
    )
    store = ProjectAuditStore(tmp_path / "audit.sqlite3")
    try:
        first = store.start_or_resume(
            thread_id="ast",
            objective="audit service",
            workspace=workspace,
            batch_size=5,
        )
        symbols = store.search_symbols(
            "charge",
            workspace=workspace,
            limit=10,
        )
        assert symbols[0]["qualified_name"] == "PaymentService.charge"
        assert symbols[0]["signature"] == "(self, order_id, *, retry)"
        summary = store.file_summary("/workspace/service.py", workspace=workspace)
        assert summary is not None
        assert "Payments service" in str(summary["summary"])

        other_workspace = tmp_path / "other-workspace"
        other_workspace.mkdir()
        (other_workspace / "other.py").write_text(
            "def charge():\n    return 'other'\n",
            encoding="utf-8",
        )
        store.start_or_resume(
            thread_id="other",
            objective="audit other service",
            workspace=other_workspace,
            batch_size=5,
        )
        isolated = store.search_symbols("charge", workspace=workspace, limit=10)
        assert {str(item["path"]) for item in isolated} == {"service.py"}

        def fail_if_reparsed(*_args: object, **_kwargs: object):
            raise AssertionError("unchanged files must use the SHA-bound cache")

        monkeypatch.setattr(project_audit_module, "_analyze_file", fail_if_reparsed)
        second = store.start_or_resume(
            thread_id="ast",
            objective="audit service",
            workspace=workspace,
            batch_size=5,
        )
        assert second.run_id == first.run_id
    finally:
        store.close()


def test_manifest_scales_to_hundreds_of_documents_and_skips_secrets(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(300):
        (workspace / f"document-{index:03d}.txt").write_text(
            f"DOCUMENT_{index:03d}\n",
            encoding="utf-8",
        )
    (workspace / ".env").write_text("SECRET=not-indexed\n", encoding="utf-8")
    (workspace / "binary.data").write_bytes(b"prefix\0binary")
    (workspace / ".venv").mkdir()
    (workspace / ".venv" / "ignored.py").write_text("SECRET", encoding="utf-8")
    (workspace / ".pytest-tmp-run").mkdir()
    (workspace / ".pytest-tmp-run" / "ignored.txt").write_text(
        "GENERATED",
        encoding="utf-8",
    )
    (workspace / "edge-profile-live").mkdir()
    (workspace / "edge-profile-live" / "history.txt").write_text(
        "BROWSER",
        encoding="utf-8",
    )

    store = ProjectAuditStore(tmp_path / "audit.sqlite3")
    try:
        progress = store.start_or_resume(
            thread_id="hundreds",
            objective="audit all documents",
            workspace=workspace,
            batch_size=10,
        )
        assert progress.total == 300
        batch = store.next_batch(progress.run_id)
        assert batch is not None
        assert batch.paths == tuple(f"document-{index:03d}.txt" for index in range(10))
    finally:
        store.close()


def test_hidden_project_paths_keep_their_exact_name(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workflow = workspace / ".github" / "workflows" / "quality.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: quality\n", encoding="utf-8")

    store = ProjectAuditStore(tmp_path / "audit.sqlite3")
    try:
        progress = store.start_or_resume(
            thread_id="hidden",
            objective="audit hidden project configuration",
            workspace=workspace,
            batch_size=5,
        )
        batch = store.next_batch(progress.run_id)
        assert batch is not None
        assert batch.paths == (".github/workflows/quality.yml",)
        completed = store.complete_batch(
            batch,
            processed_paths={"/workspace/.github/workflows/quality.yml"},
            read_counts={"/workspace/.github/workflows/quality.yml": 1},
            answer="read",
        )
        assert completed.complete
    finally:
        store.close()


def test_file_ledger_schema_is_migrated_from_early_preview(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE file_ledger (
                workspace TEXT NOT NULL,
                path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                summary TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (workspace, path)
            )
            """
        )

    store = ProjectAuditStore(database)
    store.close()
    with sqlite3.connect(database) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(file_ledger)").fetchall()
        }
    assert "modified_ns" in columns


def test_removed_file_is_purged_from_summary_and_ast_indexes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "obsolete.py"
    target.write_text("def obsolete_symbol():\n    return True\n", encoding="utf-8")
    store = ProjectAuditStore(tmp_path / "audit.sqlite3")
    try:
        store.start_or_resume(
            thread_id="removed",
            objective="audit project",
            workspace=workspace,
            batch_size=5,
        )
        assert store.search_symbols("obsolete_symbol", workspace=workspace, limit=5)

        target.unlink()
        progress = store.start_or_resume(
            thread_id="removed",
            objective="audit project",
            workspace=workspace,
            batch_size=5,
        )
        assert progress.total == 0
        assert not store.search_symbols("obsolete_symbol", workspace=workspace, limit=5)
        assert store.file_summary("obsolete.py", workspace=workspace) is None
    finally:
        store.close()
