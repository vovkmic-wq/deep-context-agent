"""Persistent, resumable project-audit manifests and structural indexes."""

from __future__ import annotations

import ast
import hashlib
import os
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_SKIPPED_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {
        ".agent_data",
        ".git",
        ".hg",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "agent-data",
        "build",
        "coverage",
        "coverage-html",
        "dist",
        "htmlcov",
        "node_modules",
        "playwright-report",
        "site-packages",
        "test-results",
        "venv",
    }
)

_SKIPPED_FILE_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.test",
    }
)

_SKIPPED_DIRECTORY_PREFIXES: Final[tuple[str, ...]] = (
    ".pytest-",
    "browser-profile",
    "edge-profile",
)

_BINARY_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".7z",
        ".avi",
        ".bin",
        ".bmp",
        ".class",
        ".db",
        ".dll",
        ".doc",
        ".docx",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".pyc",
        ".pyd",
        ".sqlite",
        ".sqlite3",
        ".tar",
        ".tiff",
        ".wav",
        ".webp",
        ".whl",
        ".xls",
        ".xlsx",
        ".zip",
    }
)

_SUMMARY_READ_LIMIT: Final[int] = 256 * 1024
_AST_READ_LIMIT: Final[int] = 2 * 1024 * 1024
_BATCH_ANSWER_LIMIT: Final[int] = 20_000


@dataclass(frozen=True, slots=True)
class AuditBatch:
    """A bounded set of project files allocated to one model invocation."""

    run_id: str
    number: int
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditProgress:
    """Cardinality and status for a persistent project audit."""

    run_id: str
    status: str
    total: int
    pending: int
    in_progress: int
    reviewed: int
    partial: int
    skipped: int
    batches: int
    file_reads: int

    @property
    def complete(self) -> bool:
        return self.pending == 0 and self.in_progress == 0


class _PythonSymbolVisitor(ast.NodeVisitor):
    """Collect qualified Python definitions without importing project code."""

    def __init__(self) -> None:
        self._parents: list[str] = []
        self.symbols: list[tuple[str, str, str, int, int, str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._append(node, "class", "")
        self._parents.append(node.name)
        self.generic_visit(node)
        self._parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, "async_function")

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        kind: str,
    ) -> None:
        signature = _format_signature(node.args)
        self._append(node, kind, signature)
        self._parents.append(node.name)
        self.generic_visit(node)
        self._parents.pop()

    def _append(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: str,
        signature: str,
    ) -> None:
        qualified_name = ".".join((*self._parents, node.name))
        docstring = (ast.get_docstring(node) or "").strip().splitlines()
        first_doc_line = docstring[0][:500] if docstring else ""
        self.symbols.append(
            (
                node.name,
                qualified_name,
                kind,
                node.lineno,
                getattr(node, "end_lineno", node.lineno),
                signature,
                first_doc_line,
            )
        )


def _format_signature(arguments: ast.arguments) -> str:
    positional = [
        argument.arg for argument in (*arguments.posonlyargs, *arguments.args)
    ]
    if arguments.vararg is not None:
        positional.append(f"*{arguments.vararg.arg}")
    elif arguments.kwonlyargs:
        positional.append("*")
    positional.extend(argument.arg for argument in arguments.kwonlyargs)
    if arguments.kwarg is not None:
        positional.append(f"**{arguments.kwarg.arg}")
    return f"({', '.join(positional)})"


class ProjectAuditStore:
    """Maintain resumable audit state, file hashes, summaries, and AST symbols."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
            timeout=30,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._initialize_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def start_or_resume(
        self,
        *,
        thread_id: str,
        objective: str,
        workspace: Path,
        batch_size: int,
    ) -> AuditProgress:
        """Create or synchronize a stable audit run for a thread and objective."""

        resolved_workspace = workspace.resolve()
        identity = "\0".join(
            (str(resolved_workspace).casefold(), thread_id, objective.strip())
        )
        run_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        now = time.time()

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO audit_runs(
                    id, thread_id, objective, workspace, batch_size,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    batch_size = excluded.batch_size,
                    status = CASE
                        WHEN audit_runs.status = 'complete' THEN 'complete'
                        ELSE 'running'
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    thread_id,
                    objective.strip(),
                    str(resolved_workspace),
                    batch_size,
                    now,
                    now,
                ),
            )
            self._connection.execute(
                "UPDATE audit_files SET status = 'pending' "
                "WHERE run_id = ? AND status = 'in_progress'",
                (run_id,),
            )

        self._synchronize_files(run_id, resolved_workspace)
        return self.progress(run_id)

    def next_batch(self, run_id: str) -> AuditBatch | None:
        """Allocate the next bounded batch and persist its in-progress state."""

        with self._lock, self._connection:
            run = self._connection.execute(
                "SELECT batch_size FROM audit_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError(f"Unknown audit run: {run_id}")

            self._connection.execute(
                "UPDATE audit_files SET status = 'pending' "
                "WHERE run_id = ? AND status = 'in_progress'",
                (run_id,),
            )
            rows = self._connection.execute(
                """
                SELECT path
                FROM audit_files
                WHERE run_id = ? AND status = 'pending'
                ORDER BY path COLLATE NOCASE
                LIMIT ?
                """,
                (run_id, int(run["batch_size"])),
            ).fetchall()
            if not rows:
                self._set_completion_status(run_id)
                return None

            batch_number = (
                int(
                    self._connection.execute(
                        "SELECT COUNT(*) FROM audit_batches WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()[0]
                )
                + 1
            )
            paths = tuple(str(row["path"]) for row in rows)
            self._connection.executemany(
                """
                UPDATE audit_files
                SET status = 'in_progress', batch_number = ?, updated_at = ?
                WHERE run_id = ? AND path = ?
                """,
                ((batch_number, time.time(), run_id, path) for path in paths),
            )
            return AuditBatch(run_id=run_id, number=batch_number, paths=paths)

    def complete_batch(
        self,
        batch: AuditBatch,
        *,
        processed_paths: set[str],
        read_counts: Mapping[str, int] | None = None,
        partial_paths: set[str] | None = None,
        answer: str,
    ) -> AuditProgress:
        """Commit evidence-backed batch progress and leave unread files pending."""

        normalized_processed = {
            _normalize_project_path(path).casefold()
            for path in processed_paths
            if path.strip()
        }
        normalized_read_counts: Counter[str] = Counter()
        for path, count in (read_counts or {}).items():
            if count > 0:
                normalized_read_counts[_normalize_project_path(path).casefold()] += (
                    count
                )
        normalized_partial = {
            _normalize_project_path(path).casefold()
            for path in (partial_paths or set())
            if path.strip()
        }
        now = time.time()
        with self._lock, self._connection:
            for path in batch.paths:
                normalized_path = path.casefold()
                if normalized_path not in normalized_processed:
                    status = "pending"
                elif normalized_path in normalized_partial:
                    status = "partial"
                else:
                    status = "reviewed"
                self._connection.execute(
                    """
                    UPDATE audit_files
                    SET status = ?, read_count = read_count + ?, updated_at = ?
                    WHERE run_id = ? AND path = ?
                    """,
                    (
                        status,
                        normalized_read_counts[path.casefold()],
                        now,
                        batch.run_id,
                        path,
                    ),
                )
            self._connection.execute(
                """
                INSERT INTO audit_batches(
                    run_id, batch_number, paths, processed_count, answer, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.run_id,
                    batch.number,
                    "\n".join(batch.paths),
                    sum(
                        path.casefold() in normalized_processed for path in batch.paths
                    ),
                    answer[:_BATCH_ANSWER_LIMIT],
                    now,
                ),
            )
            self._set_completion_status(batch.run_id)
        return self.progress(batch.run_id)

    def release_batch(self, batch: AuditBatch) -> None:
        """Return an interrupted batch to the pending queue."""

        with self._lock, self._connection:
            self._connection.executemany(
                """
                UPDATE audit_files
                SET status = 'pending', updated_at = ?
                WHERE run_id = ? AND path = ? AND status = 'in_progress'
                """,
                ((time.time(), batch.run_id, path) for path in batch.paths),
            )

    def progress(self, run_id: str) -> AuditProgress:
        with self._lock:
            run = self._connection.execute(
                "SELECT status FROM audit_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError(f"Unknown audit run: {run_id}")
            counts = {
                str(row["status"]): int(row["count"])
                for row in self._connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM audit_files
                    WHERE run_id = ?
                    GROUP BY status
                    """,
                    (run_id,),
                ).fetchall()
            }
            batches = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM audit_batches WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
            file_reads = int(
                self._connection.execute(
                    "SELECT COALESCE(SUM(read_count), 0) FROM audit_files "
                    "WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
        return AuditProgress(
            run_id=run_id,
            status=str(run["status"]),
            total=sum(counts.values()),
            pending=counts.get("pending", 0),
            in_progress=counts.get("in_progress", 0),
            reviewed=counts.get("reviewed", 0),
            partial=counts.get("partial", 0),
            skipped=counts.get("skipped", 0),
            batches=batches,
            file_reads=file_reads,
        )

    def list_runs(
        self,
        *,
        workspace: Path,
        limit: int = 10,
    ) -> list[dict[str, object]]:
        bounded_limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, thread_id, objective, workspace, status, updated_at
                FROM audit_runs
                WHERE workspace = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(workspace.resolve()), bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def file_summary(
        self,
        path: str,
        *,
        workspace: Path,
    ) -> dict[str, object] | None:
        normalized = _normalize_project_path(path)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT path, content_hash, byte_size, summary, updated_at
                FROM file_ledger
                WHERE workspace = ? AND path = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (str(workspace.resolve()), normalized),
            ).fetchone()
        return dict(row) if row is not None else None

    def search_symbols(
        self,
        query: str,
        *,
        workspace: Path,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        bounded_limit = max(1, min(limit, 100))
        escaped_query = clean_query.replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped_query}%"
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT path, name, qualified_name, kind, line_start, line_end,
                       signature, docstring
                FROM python_symbols
                WHERE workspace = ?
                  AND (
                      name LIKE ? ESCAPE '\\'
                      OR qualified_name LIKE ? ESCAPE '\\'
                      OR docstring LIKE ? ESCAPE '\\'
                  )
                ORDER BY
                    CASE WHEN name = ? THEN 0 ELSE 1 END,
                    path COLLATE NOCASE,
                    line_start
                LIMIT ?
                """,
                (
                    str(workspace.resolve()),
                    pattern,
                    pattern,
                    pattern,
                    clean_query,
                    bounded_limit,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def _initialize_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_runs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    batch_size INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_files (
                    run_id TEXT NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    batch_number INTEGER,
                    read_count INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (run_id, path)
                );

                CREATE INDEX IF NOT EXISTS idx_audit_files_status
                    ON audit_files(run_id, status, path);

                CREATE TABLE IF NOT EXISTS audit_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
                    batch_number INTEGER NOT NULL,
                    paths TEXT NOT NULL,
                    processed_count INTEGER NOT NULL,
                    answer TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS file_ledger (
                    workspace TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (workspace, path)
                );

                CREATE TABLE IF NOT EXISTS python_symbols (
                    workspace TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    signature TEXT NOT NULL,
                    docstring TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_python_symbols_name
                    ON python_symbols(name, qualified_name);
                CREATE INDEX IF NOT EXISTS idx_python_symbols_path
                    ON python_symbols(workspace, path);
                """
            )
            ledger_columns = {
                str(row[1])
                for row in self._connection.execute(
                    "PRAGMA table_info(file_ledger)"
                ).fetchall()
            }
            if "modified_ns" not in ledger_columns:
                self._connection.execute(
                    "ALTER TABLE file_ledger "
                    "ADD COLUMN modified_ns INTEGER NOT NULL DEFAULT 0"
                )

    def _synchronize_files(self, run_id: str, workspace: Path) -> None:
        workspace_key = str(workspace)
        seen: set[str] = set()
        now = time.time()

        for path in _iter_project_files(workspace):
            relative = path.relative_to(workspace).as_posix()
            try:
                stat = path.stat()
            except OSError:
                continue
            byte_size = stat.st_size
            modified_ns = stat.st_mtime_ns

            with self._lock:
                ledger_row = self._connection.execute(
                    """
                    SELECT content_hash, byte_size, modified_ns, summary
                    FROM file_ledger
                    WHERE workspace = ? AND path = ?
                    """,
                    (workspace_key, relative),
                ).fetchone()

            unchanged = (
                ledger_row is not None
                and int(ledger_row["byte_size"]) == byte_size
                and int(ledger_row["modified_ns"]) == modified_ns
            )
            if unchanged:
                content_hash = str(ledger_row["content_hash"])
                summary = str(ledger_row["summary"])
                symbols: list[tuple[str, str, str, int, int, str, str]] | None = None
            else:
                try:
                    content_hash = _sha256_file(path)
                    summary, symbols = _analyze_file(path, byte_size)
                except OSError:
                    continue
            seen.add(relative)

            with self._lock, self._connection:
                self._connection.execute(
                    """
                    INSERT INTO file_ledger(
                        workspace, path, content_hash, byte_size, modified_ns,
                        summary, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workspace, path) DO UPDATE SET
                        content_hash = excluded.content_hash,
                        byte_size = excluded.byte_size,
                        modified_ns = excluded.modified_ns,
                        summary = excluded.summary,
                        updated_at = excluded.updated_at
                    """,
                    (
                        workspace_key,
                        relative,
                        content_hash,
                        byte_size,
                        modified_ns,
                        summary,
                        now,
                    ),
                )
                existing = self._connection.execute(
                    """
                    SELECT content_hash
                    FROM audit_files
                    WHERE run_id = ? AND path = ?
                    """,
                    (run_id, relative),
                ).fetchone()
                if existing is None:
                    self._connection.execute(
                        """
                        INSERT INTO audit_files(
                            run_id, path, content_hash, byte_size, status,
                            summary, updated_at
                        ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                        """,
                        (run_id, relative, content_hash, byte_size, summary, now),
                    )
                elif existing["content_hash"] != content_hash:
                    self._connection.execute(
                        """
                        UPDATE audit_files
                        SET content_hash = ?, byte_size = ?, status = 'pending',
                            batch_number = NULL, summary = ?, updated_at = ?
                        WHERE run_id = ? AND path = ?
                        """,
                        (content_hash, byte_size, summary, now, run_id, relative),
                    )

                if symbols is not None:
                    self._connection.execute(
                        "DELETE FROM python_symbols WHERE workspace = ? AND path = ?",
                        (workspace_key, relative),
                    )
                    self._connection.executemany(
                        """
                        INSERT INTO python_symbols(
                            workspace, path, content_hash, name, qualified_name,
                            kind, line_start, line_end, signature, docstring
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            (workspace_key, relative, content_hash, *symbol)
                            for symbol in symbols
                        ),
                    )

        with self._lock, self._connection:
            known_paths = {
                str(row["path"])
                for row in self._connection.execute(
                    "SELECT path FROM audit_files WHERE run_id = ?",
                    (run_id,),
                ).fetchall()
            }
            missing = known_paths - seen
            ledger_paths = {
                str(row["path"])
                for row in self._connection.execute(
                    "SELECT path FROM file_ledger WHERE workspace = ?",
                    (workspace_key,),
                ).fetchall()
            }
            stale_ledger_paths = ledger_paths - seen
            self._connection.executemany(
                "DELETE FROM audit_files WHERE run_id = ? AND path = ?",
                ((run_id, path) for path in missing),
            )
            self._connection.executemany(
                "DELETE FROM file_ledger WHERE workspace = ? AND path = ?",
                ((workspace_key, path) for path in stale_ledger_paths),
            )
            self._connection.executemany(
                "DELETE FROM python_symbols WHERE workspace = ? AND path = ?",
                ((workspace_key, path) for path in stale_ledger_paths),
            )
            self._connection.execute(
                "UPDATE audit_runs SET status = 'running', updated_at = ? WHERE id = ?",
                (now, run_id),
            )
            self._set_completion_status(run_id)

    def _set_completion_status(self, run_id: str) -> None:
        unfinished = int(
            self._connection.execute(
                """
                SELECT COUNT(*)
                FROM audit_files
                WHERE run_id = ? AND status IN ('pending', 'in_progress')
                """,
                (run_id,),
            ).fetchone()[0]
        )
        partial = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM audit_files "
                "WHERE run_id = ? AND status = 'partial'",
                (run_id,),
            ).fetchone()[0]
        )
        if unfinished:
            status = "running"
        elif partial:
            status = "complete_with_partial"
        else:
            status = "complete"
        self._connection.execute(
            "UPDATE audit_runs SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), run_id),
        )


def _iter_project_files(workspace: Path) -> Iterator[Path]:
    def ignore_walk_error(_error: OSError) -> None:
        return None

    for root, directory_names, file_names in os.walk(
        workspace,
        topdown=True,
        onerror=ignore_walk_error,
        followlinks=False,
    ):
        root_path = Path(root)
        directory_names[:] = sorted(
            (
                name
                for name in directory_names
                if not _is_skipped_directory(name)
                and not (root_path / name).is_symlink()
            ),
            key=str.casefold,
        )
        for name in sorted(file_names, key=str.casefold):
            path = root_path / name
            if path.is_symlink() or _is_skipped_file(name):
                continue
            if path.suffix.casefold() in _BINARY_SUFFIXES:
                continue
            try:
                with path.open("rb") as stream:
                    probe = stream.read(4_096)
                if b"\0" in probe:
                    continue
            except OSError:
                continue
            yield path


def _is_skipped_directory(name: str) -> bool:
    normalized = name.casefold()
    return normalized in _SKIPPED_DIRECTORIES or normalized.startswith(
        _SKIPPED_DIRECTORY_PREFIXES
    )


def _is_skipped_file(name: str) -> bool:
    normalized = name.casefold()
    return (
        normalized in _SKIPPED_FILE_NAMES
        or normalized == ".coverage"
        or normalized.startswith(".coverage.")
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _analyze_file(
    path: Path,
    byte_size: int,
) -> tuple[str, list[tuple[str, str, str, int, int, str, str]]]:
    with path.open("rb") as stream:
        read_limit = max(
            _SUMMARY_READ_LIMIT,
            _AST_READ_LIMIT if path.suffix == ".py" else 0,
        )
        raw = stream.read(read_limit)
    text = _decode_text(raw)
    symbols: list[tuple[str, str, str, int, int, str, str]] = []
    details: list[str] = [f"size={byte_size} bytes"]

    if path.suffix.casefold() == ".py" and byte_size <= _AST_READ_LIMIT:
        try:
            tree = ast.parse(text, filename=path.name)
        except (SyntaxError, ValueError) as error:
            details.append(f"python_ast_error={type(error).__name__}: {error}")
        else:
            module_doc = (ast.get_docstring(tree) or "").strip().splitlines()
            if module_doc:
                details.append(f"module={module_doc[0][:500]}")
            visitor = _PythonSymbolVisitor()
            visitor.visit(tree)
            symbols = visitor.symbols
            if symbols:
                rendered = ", ".join(
                    f"{kind} {qualified_name}{signature}"
                    for _, qualified_name, kind, _, _, signature, _ in symbols[:50]
                )
                details.append(f"symbols={rendered}")

    nonempty = [
        line.strip() for line in text[:_SUMMARY_READ_LIMIT].splitlines() if line.strip()
    ]
    if nonempty:
        details.append("preview=" + " | ".join(nonempty[:5])[:1_500])
    return "\n".join(details)[:4_000], symbols


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _normalize_project_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("/workspace/"):
        normalized = normalized[len("/workspace/") :]
    elif normalized == "/workspace":
        normalized = ""
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")
