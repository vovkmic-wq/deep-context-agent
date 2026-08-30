"""Persistent SQLite FTS5 storage for large searchable context."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from context_agent.errors import ContextStoreError, PathSecurityError
from context_agent.paths import resolve_inside

_TOKEN_PATTERN = re.compile(r"[^\W_]{2,}", flags=re.UNICODE)
_SKIPPED_DIRECTORIES = {
    ".agent_data",
    ".diagnostic-exports",
    ".cache",
    ".deps",
    ".git",
    ".hg",
    ".hypothesis",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "playwright-report",
    "reports",
    "site-packages",
    "test-results",
}
_SKIPPED_DIRECTORY_PREFIXES = (
    ".pytest-",
    ".pytest_",
    "browser-profile",
    "chrome-profile",
    "edge-profile",
)
_SKIPPED_DIRECTORY_SUFFIXES = (".egg-info",)
_SKIPPED_FILENAMES = {
    ".coverage",
    ".env",
    ".env.local",
    "context-agent-server.jsonl",
    "diagnostics.sqlite3",
    "diagnostics.sqlite3-shm",
    "diagnostics.sqlite3-wal",
}
_SKIPPED_FILENAME_PREFIXES = (
    ".coverage.",
    "context-agent-server.jsonl.",
    "diagnostic-export",
)
_TEXT_EXTENSIONS = {
    "",
    ".cfg",
    ".csv",
    ".html",
    ".ini",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".py",
    ".rst",
    ".sql",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One retrieved context fragment."""

    source: str
    kind: str
    content: str
    chunk_index: int
    score: float


@dataclass(frozen=True, slots=True)
class ContextSource:
    """Metadata for one indexed document or conversation message."""

    source: str
    kind: str
    byte_size: int
    chunk_count: int
    indexed_at: str


@dataclass(frozen=True, slots=True)
class IndexReport:
    """Summary of a file or directory indexing operation."""

    files_indexed: int = 0
    files_unchanged: int = 0
    files_skipped: int = 0
    chunks_written: int = 0
    errors: tuple[str, ...] = ()


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into bounded, overlapping chunks near natural boundaries."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        target_end = min(start + chunk_size, text_length)
        end = target_end
        if target_end < text_length:
            lower_bound = start + (chunk_size * 3 // 5)
            newline = text.rfind("\n", lower_bound, target_end)
            space = text.rfind(" ", lower_bound, target_end)
            boundary = max(newline, space)
            if boundary > start:
                end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break
        next_start = max(0, end - overlap)
        start = next_start if next_start > start else end
    return chunks


class ContextStore:
    """Thread-safe persistent document, conversation, and FTS5 index."""

    def __init__(
        self,
        database_path: Path,
        *,
        chunk_size: int = 4_000,
        chunk_overlap: int = 400,
        max_file_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> None:
        self.database_path = database_path.resolve()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_file_bytes = max_file_bytes
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._closed = False
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        schema = """
        PRAGMA foreign_keys = ON;
        PRAGMA journal_mode = WAL;

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            modified_ns INTEGER,
            byte_size INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id)
                ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            UNIQUE(document_id, chunk_index)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            content,
            content='chunks',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER IF NOT EXISTS chunks_after_insert AFTER INSERT ON chunks
        BEGIN
            INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
        END;

        CREATE TRIGGER IF NOT EXISTS chunks_after_delete AFTER DELETE ON chunks
        BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
        END;

        CREATE TRIGGER IF NOT EXISTS chunks_after_update AFTER UPDATE ON chunks
        BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
            INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
        END;
        """
        try:
            with self._lock:
                self._connection.executescript(schema)
                self._connection.commit()
        except sqlite3.Error as exc:
            raise ContextStoreError(
                "SQLite FTS5 is required but could not be initialized"
            ) from exc

    def close(self) -> None:
        """Flush pending changes and close the database."""
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> ContextStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add_text(
        self,
        source: str,
        text: str,
        *,
        kind: str = "document",
        modified_ns: int | None = None,
        byte_size: int | None = None,
    ) -> tuple[bool, int]:
        """Insert or replace one source and return ``(changed, chunk_count)``."""
        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
        digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        chunks = chunk_text(
            normalized_text,
            self.chunk_size,
            self.chunk_overlap,
        )
        stored_size = byte_size
        if stored_size is None:
            stored_size = len(normalized_text.encode("utf-8"))

        with self._lock:
            current = self._connection.execute(
                "SELECT content_hash FROM documents WHERE source = ?",
                (source,),
            ).fetchone()
            if current and current["content_hash"] == digest:
                return False, 0
            try:
                with self._connection:
                    self._connection.execute(
                        "DELETE FROM documents WHERE source = ?",
                        (source,),
                    )
                    cursor = self._connection.execute(
                        """
                        INSERT INTO documents(
                            source, kind, content_hash, modified_ns,
                            byte_size, indexed_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source,
                            kind,
                            digest,
                            modified_ns,
                            stored_size,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                    if cursor.lastrowid is None:
                        raise sqlite3.DatabaseError(
                            "SQLite did not return an inserted document ID"
                        )
                    document_id = cursor.lastrowid
                    self._connection.executemany(
                        """
                        INSERT INTO chunks(document_id, chunk_index, content)
                        VALUES (?, ?, ?)
                        """,
                        (
                            (document_id, index, chunk)
                            for index, chunk in enumerate(chunks)
                        ),
                    )
            except sqlite3.Error as exc:
                raise ContextStoreError(f"Cannot index source '{source}'") from exc
        return True, len(chunks)

    def archive_message(self, thread_id: str, role: str, content: str) -> None:
        """Persist one conversation message as searchable long-term context."""
        safe_thread = re.sub(r"[^a-zA-Z0-9_.-]", "_", thread_id)[:100]
        source = f"conversation://{safe_thread}/{time.time_ns()}-{uuid4().hex}/{role}"
        self.add_text(source, content, kind="conversation")

    def add_file(
        self,
        source: str,
        path: Path,
        *,
        modified_ns: int,
        byte_size: int,
    ) -> tuple[bool, int]:
        """Stream one file into FTS5 and return ``(changed, chunk_count)``."""
        with self._lock:
            current = self._connection.execute(
                """
                SELECT content_hash, modified_ns, byte_size
                FROM documents WHERE source = ?
                """,
                (source,),
            ).fetchone()
            if (
                current
                and current["modified_ns"] == modified_ns
                and current["byte_size"] == byte_size
            ):
                return False, 0

        digest = _file_sha256(path)
        with self._lock:
            if current and current["content_hash"] == digest:
                with self._connection:
                    self._connection.execute(
                        """
                        UPDATE documents
                        SET modified_ns = ?, byte_size = ?, indexed_at = ?
                        WHERE source = ?
                        """,
                        (
                            modified_ns,
                            byte_size,
                            datetime.now(UTC).isoformat(),
                            source,
                        ),
                    )
                return False, 0

            chunk_count = 0
            try:
                with self._connection:
                    self._connection.execute(
                        "DELETE FROM documents WHERE source = ?",
                        (source,),
                    )
                    cursor = self._connection.execute(
                        """
                        INSERT INTO documents(
                            source, kind, content_hash, modified_ns,
                            byte_size, indexed_at
                        ) VALUES (?, 'file', ?, ?, ?, ?)
                        """,
                        (
                            source,
                            digest,
                            modified_ns,
                            byte_size,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                    if cursor.lastrowid is None:
                        raise sqlite3.DatabaseError(
                            "SQLite did not return an inserted document ID"
                        )
                    document_id = cursor.lastrowid
                    batch: list[tuple[int, int, str]] = []
                    for chunk_index, chunk in enumerate(
                        _iter_file_chunks(
                            path,
                            self.chunk_size,
                            self.chunk_overlap,
                        )
                    ):
                        batch.append((document_id, chunk_index, chunk))
                        chunk_count += 1
                        if len(batch) >= 500:
                            self._write_chunk_batch(batch)
                            batch.clear()
                    if batch:
                        self._write_chunk_batch(batch)
            except (OSError, UnicodeError, sqlite3.Error) as exc:
                raise ContextStoreError(f"Cannot index source '{source}'") from exc
        return True, chunk_count

    def _write_chunk_batch(self, batch: list[tuple[int, int, str]]) -> None:
        self._connection.executemany(
            """
            INSERT INTO chunks(document_id, chunk_index, content)
            VALUES (?, ?, ?)
            """,
            batch,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        source: str | None = None,
    ) -> list[SearchHit]:
        """Search indexed context using FTS5 and BM25 ranking."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        match_expression = _fts_expression(query)
        if not match_expression:
            return []
        statement = """
            SELECT
                documents.source,
                documents.kind,
                chunks.content,
                chunks.chunk_index,
                bm25(chunks_fts) AS rank
            FROM chunks_fts
            JOIN chunks ON chunks.id = chunks_fts.rowid
            JOIN documents ON documents.id = chunks.document_id
            WHERE chunks_fts MATCH ?
        """
        parameters: list[object] = [match_expression]
        if source:
            statement += " AND documents.source = ?"
            parameters.append(source)
        statement += " ORDER BY rank LIMIT ?"
        parameters.append(limit)
        try:
            with self._lock:
                rows = self._connection.execute(
                    statement,
                    parameters,
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContextStoreError("Context search failed") from exc
        return [
            SearchHit(
                source=row["source"],
                kind=row["kind"],
                content=row["content"],
                chunk_index=int(row["chunk_index"]),
                score=-float(row["rank"]),
            )
            for row in rows
        ]

    def context_window(
        self,
        source: str,
        chunk_index: int,
        *,
        radius: int = 2,
    ) -> list[SearchHit]:
        """Return a matching chunk and its ordered neighbors from one source."""
        if chunk_index < 0:
            raise ValueError("chunk_index cannot be negative")
        if not 0 <= radius <= 20:
            raise ValueError("radius must be between 0 and 20")
        first = max(0, chunk_index - radius)
        last = chunk_index + radius
        statement = """
            SELECT documents.source, documents.kind, chunks.content,
                   chunks.chunk_index
            FROM chunks
            JOIN documents ON documents.id = chunks.document_id
            WHERE documents.source = ? AND chunks.chunk_index BETWEEN ? AND ?
            ORDER BY chunks.chunk_index
        """
        with self._lock:
            rows = self._connection.execute(
                statement,
                (source, first, last),
            ).fetchall()
        return [
            SearchHit(
                source=row["source"],
                kind=row["kind"],
                content=row["content"],
                chunk_index=int(row["chunk_index"]),
                score=0.0,
            )
            for row in rows
        ]

    def list_sources(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        kind: str | None = None,
    ) -> list[ContextSource]:
        """List indexed sources in pages so hundreds of documents stay browsable."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        statement = """
            SELECT documents.source, documents.kind, documents.byte_size,
                   documents.indexed_at, COUNT(chunks.id) AS chunk_count
            FROM documents
            LEFT JOIN chunks ON chunks.document_id = documents.id
        """
        parameters: list[object] = []
        if kind:
            statement += " WHERE documents.kind = ?"
            parameters.append(kind)
        statement += """
            GROUP BY documents.id
            ORDER BY documents.source
            LIMIT ? OFFSET ?
        """
        parameters.extend((limit, offset))
        with self._lock:
            rows = self._connection.execute(statement, parameters).fetchall()
        return [
            ContextSource(
                source=row["source"],
                kind=row["kind"],
                byte_size=int(row["byte_size"]),
                chunk_count=int(row["chunk_count"]),
                indexed_at=row["indexed_at"],
            )
            for row in rows
        ]

    def list_threads(self, *, limit: int = 100) -> list[dict[str, object]]:
        """List archived thread identifiers without returning message bodies."""

        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT source, indexed_at
                FROM documents
                WHERE kind = 'conversation' AND source LIKE 'conversation://%'
                ORDER BY indexed_at DESC
                LIMIT 10000
                """
            ).fetchall()
        threads: dict[str, dict[str, object]] = {}
        for row in rows:
            source = str(row["source"])
            tail = source.removeprefix("conversation://")
            thread_id = tail.split("/", 1)[0]
            current = threads.setdefault(
                thread_id,
                {
                    "thread_id": thread_id,
                    "message_count": 0,
                    "updated_at": str(row["indexed_at"]),
                },
            )
            current["message_count"] = int(str(current["message_count"])) + 1
        return list(threads.values())[:limit]

    def thread_messages(
        self,
        thread_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """Return one bounded page of archived messages for a safe thread ID."""

        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        safe_thread = re.sub(r"[^a-zA-Z0-9_.-]", "_", thread_id)[:100]
        pattern = f"conversation://{safe_thread}/%"
        with self._lock:
            documents = self._connection.execute(
                """
                SELECT id, source, indexed_at
                FROM documents
                WHERE kind = 'conversation' AND source LIKE ?
                ORDER BY indexed_at
                LIMIT ? OFFSET ?
                """,
                (pattern, limit, offset),
            ).fetchall()
            messages: list[dict[str, object]] = []
            for document in documents:
                chunks = self._connection.execute(
                    """
                    SELECT content FROM chunks
                    WHERE document_id = ? ORDER BY chunk_index
                    """,
                    (document["id"],),
                ).fetchall()
                source = str(document["source"])
                messages.append(
                    {
                        "role": source.rsplit("/", 1)[-1],
                        "content": _merge_overlapping_chunks(
                            [str(chunk["content"]) for chunk in chunks],
                            self.chunk_overlap,
                        ),
                        "indexed_at": str(document["indexed_at"]),
                    }
                )
        return messages

    def index_path(self, requested: str | Path, allowed_root: Path) -> IndexReport:
        """Index a text file or tree while enforcing an allowed source root."""
        try:
            path = resolve_inside(allowed_root, requested, must_exist=True)
        except PathSecurityError:
            raise
        except OSError as exc:
            raise ContextStoreError(f"Cannot access context path: {requested}") from exc

        if path.is_file():
            return self._index_files((path,), allowed_root.resolve())
        if not path.is_dir():
            raise ContextStoreError(f"Context path is not a file or directory: {path}")
        return self._index_files(_iter_text_files(path, allowed_root), allowed_root)

    def _index_files(
        self,
        files: Iterable[Path],
        allowed_root: Path,
    ) -> IndexReport:
        indexed = unchanged = skipped = chunks_written = 0
        errors: list[str] = []
        for file_path in files:
            if _should_skip(file_path, allowed_root):
                skipped += 1
                continue
            try:
                safe_file = resolve_inside(
                    allowed_root,
                    file_path,
                    must_exist=True,
                )
                stat = safe_file.stat()
                if stat.st_size > self.max_file_bytes:
                    raise ContextStoreError(f"file exceeds {self.max_file_bytes} bytes")
                relative = safe_file.relative_to(allowed_root).as_posix()
                changed, chunk_count = self.add_file(
                    f"file://{relative}",
                    safe_file,
                    modified_ns=stat.st_mtime_ns,
                    byte_size=stat.st_size,
                )
                if changed:
                    indexed += 1
                    chunks_written += chunk_count
                else:
                    unchanged += 1
            except (OSError, UnicodeError, ContextStoreError, PathSecurityError) as exc:
                errors.append(f"{file_path}: {exc}")
        return IndexReport(
            files_indexed=indexed,
            files_unchanged=unchanged,
            files_skipped=skipped,
            chunks_written=chunks_written,
            errors=tuple(errors),
        )


def _fts_expression(query: str) -> str:
    tokens = _TOKEN_PATTERN.findall(query.casefold())[:24]
    unique_tokens = list(dict.fromkeys(tokens))
    return " OR ".join(f'"{token}"' for token in unique_tokens)


def _iter_text_files(path: Path, allowed_root: Path) -> Iterator[Path]:
    for root, directory_names, file_names in os.walk(path, followlinks=False):
        root_path = Path(root)
        directory_names[:] = [
            name
            for name in directory_names
            if not _should_skip_directory(root_path / name, allowed_root)
        ]
        for file_name in file_names:
            candidate = root_path / file_name
            if candidate.is_file() and not _should_skip(candidate, allowed_root):
                yield candidate


def _should_skip_directory(path: Path, allowed_root: Path) -> bool:
    """Return whether a directory is generated, cached, or secret-bearing."""

    try:
        relative = path.relative_to(allowed_root)
    except ValueError:
        return True
    for part in relative.parts:
        normalized = part.casefold()
        if (
            normalized in _SKIPPED_DIRECTORIES
            or normalized.startswith(_SKIPPED_DIRECTORY_PREFIXES)
            or normalized.endswith(_SKIPPED_DIRECTORY_SUFFIXES)
        ):
            return True
    return False


def _should_skip(path: Path, allowed_root: Path) -> bool:
    try:
        relative = path.relative_to(allowed_root)
    except ValueError:
        return True
    if any(
        part.casefold() in _SKIPPED_DIRECTORIES
        or part.casefold().startswith(_SKIPPED_DIRECTORY_PREFIXES)
        or part.casefold().endswith(_SKIPPED_DIRECTORY_SUFFIXES)
        for part in relative.parts[:-1]
    ):
        return True
    normalized_name = path.name.casefold()
    if (
        normalized_name in _SKIPPED_FILENAMES
        or normalized_name.startswith(".env.")
        or normalized_name.startswith(_SKIPPED_FILENAME_PREFIXES)
    ):
        return True
    return path.suffix.casefold() not in _TEXT_EXTENSIONS


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _merge_overlapping_chunks(chunks: list[str], overlap: int) -> str:
    """Reconstruct archived text without duplicating configured overlap."""

    if not chunks:
        return ""
    merged = chunks[0]
    for chunk in chunks[1:]:
        shared = min(overlap, len(merged), len(chunk))
        while shared and not merged.endswith(chunk[:shared]):
            shared -= 1
        merged += chunk[shared:]
    return merged


def _detect_encoding(path: Path) -> str:
    with path.open("rb") as stream:
        sample = stream.read(1024 * 1024)
    if sample.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32"
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if b"\x00" in sample:
        raise ContextStoreError("binary file is not indexable")
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise ContextStoreError("file encoding is neither UTF-8 nor CP1251")


def _iter_file_chunks(
    path: Path,
    chunk_size: int,
    overlap: int,
) -> Iterator[str]:
    encoding = _detect_encoding(path)
    buffer = ""
    with path.open("r", encoding=encoding, newline=None) as stream:
        while block := stream.read(max(64 * 1024, chunk_size * 4)):
            buffer += block
            while len(buffer) >= chunk_size:
                end = _chunk_boundary(buffer, chunk_size)
                chunk = buffer[:end].strip()
                if chunk:
                    yield chunk
                buffer = buffer[max(0, end - overlap) :]
        if buffer.strip():
            yield buffer.strip()


def _chunk_boundary(buffer: str, chunk_size: int) -> int:
    lower_bound = chunk_size * 3 // 5
    newline = buffer.rfind("\n", lower_bound, chunk_size)
    space = buffer.rfind(" ", lower_bound, chunk_size)
    boundary = max(newline, space)
    return boundary + 1 if boundary >= lower_bound else chunk_size
