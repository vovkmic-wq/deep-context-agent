"""Tests for persistent, streaming, large-scale context retrieval."""

from pathlib import Path

import pytest

from context_agent.context_store import ContextStore, chunk_text
from context_agent.errors import PathSecurityError


def test_chunk_text_has_overlap_and_preserves_ends() -> None:
    text = "BEGIN " + ("middle word " * 30) + " END"
    chunks = chunk_text(text, chunk_size=80, overlap=10)
    assert chunks[0].startswith("BEGIN")
    assert chunks[-1].endswith("END")
    assert len(chunks) > 2


def test_context_persists_and_searches_after_reopen(tmp_path: Path) -> None:
    database = tmp_path / "context.sqlite3"
    with ContextStore(database, chunk_size=80, chunk_overlap=10) as store:
        changed, count = store.add_text(
            "memory://one",
            "The launch codename is Aurora and the deadline is Friday.",
        )
        assert changed is True
        assert count == 1

    with ContextStore(database, chunk_size=80, chunk_overlap=10) as store:
        hits = store.search("Aurora deadline")
        assert hits
        assert hits[0].source == "memory://one"
        unchanged, count = store.add_text(
            "memory://one",
            "The launch codename is Aurora and the deadline is Friday.",
        )
        assert unchanged is False
        assert count == 0


def test_streaming_file_index_search_filter_and_window(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    document = source_root / "manual.txt"
    document.write_text(
        "BEGINNING_ANCHOR alpha\n"
        + "ordinary context line\n" * 300
        + "ENDING_ANCHOR omega\n",
        encoding="utf-8",
    )
    database = tmp_path / "context.sqlite3"
    with ContextStore(database, chunk_size=300, chunk_overlap=40) as store:
        report = store.index_path("manual.txt", source_root)
        assert report.files_indexed == 1
        assert report.chunks_written > 10
        assert store.search("BEGINNING_ANCHOR")[0].chunk_index == 0
        ending = store.search("ENDING_ANCHOR", source="file://manual.txt")
        assert ending
        window = store.context_window(
            ending[0].source,
            ending[0].chunk_index,
            radius=1,
        )
        assert len(window) >= 2
        assert window[-1].content.endswith("ENDING_ANCHOR omega")
        second_report = store.index_path("manual.txt", source_root)
        assert second_report.files_unchanged == 1


def test_hundreds_of_documents_are_paginated_and_searchable(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    for index in range(200):
        (source_root / f"document-{index:03}.txt").write_text(
            f"Document {index} contains TOKEN_{index:03} and shared material.",
            encoding="utf-8",
        )
    with ContextStore(tmp_path / "context.sqlite3") as store:
        report = store.index_path(".", source_root)
        assert report.files_indexed == 200
        assert len(store.list_sources(limit=100, offset=0)) == 100
        assert len(store.list_sources(limit=100, offset=100)) == 100
        hits = store.search("TOKEN_173")
        assert hits[0].source == "file://document-173.txt"


def test_generated_and_browser_artifacts_are_not_indexed(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    (source_root / "src").mkdir()
    (source_root / "src" / "app.py").write_text(
        "print('application source')\n",
        encoding="utf-8",
    )
    (source_root / "pyproject.toml").write_text(
        "[project]\nname = 'example'\n",
        encoding="utf-8",
    )
    generated_files = {
        ".pytest-localization-042/reports/report.json": "irrelevant report",
        "e2e/EDGE-PROFILE2/Default/History.txt": "browser history",
        "playwright-report/index.html": "generated report",
        ".venv/package.py": "generated environment",
        ".deps/vendor.py": "dependency copy",
        "reports/generated.json": "generated report",
        "example.egg-info/PKG-INFO": "package metadata",
        "diagnostics.sqlite3": "durable journal fixture",
        "diagnostics.sqlite3-wal": "journal wal fixture",
        "context-agent-server.jsonl": "structured log fixture",
        "context-agent-server.jsonl.1": "rotated log fixture",
        "diagnostic-export-1.json": "operator export fixture",
    }
    for relative_path, content in generated_files.items():
        target = source_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (source_root / ".coverage").write_bytes(b"coverage\x00binary")

    with ContextStore(tmp_path / "context.sqlite3") as store:
        report = store.index_path(".", source_root)
        sources = {entry.source for entry in store.list_sources(limit=100)}

    assert report.files_indexed == 2
    assert report.errors == ()
    assert sources == {"file://pyproject.toml", "file://src/app.py"}


def test_one_million_lines_keep_beginning_and_end_searchable(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    document = source_root / "million-lines.txt"
    with document.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("FIRST_MILLION_LINE_ANCHOR\n")
        for _ in range(999_999):
            stream.write("ordinary scalable context line\n")
        stream.write("LAST_MILLION_LINE_ANCHOR\n")

    with ContextStore(
        tmp_path / "large.sqlite3",
        chunk_size=8_000,
        chunk_overlap=400,
        max_file_bytes=100 * 1024 * 1024,
    ) as store:
        report = store.index_path(document, source_root)
        assert report.files_indexed == 1
        assert report.chunks_written > 1_000
        first = store.search("FIRST_MILLION_LINE_ANCHOR")
        last = store.search("LAST_MILLION_LINE_ANCHOR")
        assert first[0].chunk_index == 0
        assert last[0].chunk_index == report.chunks_written - 1


def test_binary_file_becomes_reported_error(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    (source_root / "broken.txt").write_bytes(b"text\x00binary")
    with ContextStore(tmp_path / "context.sqlite3") as store:
        report = store.index_path("broken.txt", source_root)
    assert report.files_indexed == 0
    assert "binary file" in report.errors[0]


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_bom_encoded_text_is_indexed(tmp_path: Path, encoding: str) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    document = source_root / "report.txt"
    document.write_text(
        "OZON_UTF_REPORT_ANCHOR product analytics\n",
        encoding=encoding,
    )

    with ContextStore(tmp_path / "context.sqlite3") as store:
        report = store.index_path(document, source_root)
        hits = store.search("OZON_UTF_REPORT_ANCHOR")

    assert report.files_indexed == 1
    assert report.errors == ()
    assert hits[0].source == "file://report.txt"


def test_context_path_outside_root_is_rejected(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with (
        ContextStore(tmp_path / "context.sqlite3") as store,
        pytest.raises(PathSecurityError),
    ):
        store.index_path(outside, source_root)


def test_archived_threads_and_long_messages_are_reconstructed_without_overlap(
    tmp_path: Path,
) -> None:
    content = "START_" + ("x" * 240) + "_END"
    with ContextStore(
        tmp_path / "context.sqlite3",
        chunk_size=80,
        chunk_overlap=20,
    ) as store:
        store.archive_message("web-thread", "user", content)
        threads = store.list_threads()
        messages = store.thread_messages("web-thread")

    assert threads[0]["thread_id"] == "web-thread"
    assert threads[0]["message_count"] == 1
    assert messages == [
        {
            "role": "user",
            "content": content,
            "indexed_at": messages[0]["indexed_at"],
        }
    ]
