"""Persistent, resumable project-audit manifests and structural indexes."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

_SKIPPED_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {
        ".agent_data",
        ".deps",
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
        "reports",
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
    ".pytest_",
    "browser-profile",
    "edge-profile",
)

_SKIPPED_DIRECTORY_SUFFIXES: Final[tuple[str, ...]] = (".egg-info",)

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
_REPORT_LIMIT: Final[int] = 20_000
_REQUIREMENT_LINE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(?P<text>.+?)\s*$"
)
_REQUIREMENT_TERM_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?iu)(?:\b(?:must|shall|required|should)\b|долж(?:ен|на|но|ны)|"
    r"обязательн|требу(?:ет|ется)|необходимо)"
)
_AUDIT_FINDINGS_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"<audit_findings>\s*(\[.*?])\s*</audit_findings>",
    re.DOTALL,
)
_FINDING_SEVERITIES: Final[frozenset[str]] = frozenset(
    {"critical", "high", "medium", "low", "info"}
)


@dataclass(frozen=True, slots=True)
class AuditSelectionRules:
    """Deterministic include/exclude rules for one audit identity."""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuditFileSelection:
    """Selected files plus compact exclusion accounting."""

    paths: tuple[Path, ...]
    excluded: int = 0
    reasons: Mapping[str, int] = field(default_factory=dict)


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
    mode: str = "read-only"
    excluded: int = 0

    @property
    def complete(self) -> bool:
        return self.pending == 0 and self.in_progress == 0

    def as_dict(self) -> dict[str, object]:
        """Return a stable JSON-serializable status payload."""

        return {
            "run_id": self.run_id,
            "status": self.status,
            "mode": self.mode,
            "total": self.total,
            "excluded": self.excluded,
            "pending": self.pending,
            "in_progress": self.in_progress,
            "reviewed": self.reviewed,
            "partial": self.partial,
            "skipped": self.skipped,
            "batches": self.batches,
            "file_reads": self.file_reads,
            "complete": self.complete,
        }


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

    def __enter__(self) -> ProjectAuditStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start_or_resume(
        self,
        *,
        thread_id: str,
        objective: str,
        workspace: Path,
        batch_size: int,
        allow_write: bool = False,
        selection_rules: AuditSelectionRules | None = None,
    ) -> AuditProgress:
        """Create or synchronize a stable audit run for a thread and objective."""

        resolved_workspace = workspace.resolve()
        rules = selection_rules or AuditSelectionRules()
        mode = "allow-write" if allow_write else "read-only"
        identity = "\0".join(
            (
                str(resolved_workspace).casefold(),
                thread_id,
                objective.strip(),
                mode,
                json.dumps(rules.include, ensure_ascii=False),
                json.dumps(rules.exclude, ensure_ascii=False),
            )
        )
        run_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        now = time.time()

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO audit_runs(
                    id, thread_id, objective, workspace, batch_size,
                    status, mode, include_patterns, exclude_patterns,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    batch_size = excluded.batch_size,
                    mode = excluded.mode,
                    include_patterns = excluded.include_patterns,
                    exclude_patterns = excluded.exclude_patterns,
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
                    mode,
                    json.dumps(rules.include, ensure_ascii=False),
                    json.dumps(rules.exclude, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self._connection.execute(
                "UPDATE audit_files SET status = 'pending' "
                "WHERE run_id = ? AND status = 'in_progress'",
                (run_id,),
            )
        selection = select_project_files(resolved_workspace, rules)
        self._synchronize_files(run_id, resolved_workspace, selection)
        self._synchronize_requirements(run_id, resolved_workspace, objective)
        return self.progress(run_id)

    def next_batch(self, run_id: str) -> AuditBatch | None:
        """Allocate the next bounded batch and persist its in-progress state."""

        with self._lock, self._connection:
            run = self._connection.execute(
                "SELECT batch_size, status FROM audit_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError(f"Unknown audit run: {run_id}")
            if str(run["status"]) in {"paused", "cancelled"}:
                return None

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
                "SELECT status, mode, excluded_count FROM audit_runs WHERE id = ?",
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
            mode=str(run["mode"]),
            excluded=int(run["excluded_count"]),
        )

    def run_details(self, run_id: str) -> dict[str, object]:
        """Return persisted run metadata without invoking a model."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM audit_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown audit run: {run_id}")
        details = dict(row)
        for key in ("include_patterns", "exclude_patterns", "exclusion_summary"):
            try:
                details[key] = json.loads(str(details.get(key) or "null"))
            except json.JSONDecodeError:
                details[key] = None
        details["progress"] = self.progress(run_id).as_dict()
        return details

    def set_run_status(self, run_id: str, status: str) -> AuditProgress:
        """Persist a safe control status for pause/cancel/resume workflows."""

        if status not in {"running", "paused", "cancelled"}:
            raise ValueError("Unsupported audit control status")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE audit_runs SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), run_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Unknown audit run: {run_id}")
            if status in {"paused", "cancelled"}:
                self._connection.execute(
                    "UPDATE audit_files SET status = 'pending' "
                    "WHERE run_id = ? AND status = 'in_progress'",
                    (run_id,),
                )
        return self.progress(run_id)

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
                SELECT id, thread_id, objective, workspace, status, mode,
                       selected_count, excluded_count, updated_at
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

    def requirements_for_paths(
        self,
        run_id: str,
        paths: tuple[str, ...],
        *,
        limit: int = 12,
    ) -> list[dict[str, object]]:
        """Select a bounded requirement subset relevant to a file batch."""

        tokens = {
            token
            for path in paths
            for token in re.findall(r"[\w-]{3,}", path.casefold())
        }
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT requirement_id, source, section, text, level, source_hash
                FROM audit_requirements
                WHERE run_id = ?
                ORDER BY requirement_id
                """,
                (run_id,),
            ).fetchall()
        scored: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            searchable = f"{row['section']} {row['text']}".casefold()
            score = sum(token in searchable for token in tokens)
            scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], str(item[1]["requirement_id"])))
        bounded = max(1, min(limit, 50))
        return [dict(row) for _, row in scored[:bounded]]

    def record_batch_evidence(
        self,
        batch: AuditBatch,
        answer: str,
    ) -> None:
        """Persist structured findings and explicit requirement references."""

        now = time.time()
        with self._lock, self._connection:
            requirements = self._connection.execute(
                "SELECT requirement_id FROM audit_requirements WHERE run_id = ?",
                (batch.run_id,),
            ).fetchall()
            for row in requirements:
                requirement_id = str(row["requirement_id"])
                if requirement_id not in answer:
                    continue
                self._connection.execute(
                    """
                    INSERT INTO audit_requirement_evidence(
                        run_id, requirement_id, batch_number, status,
                        evidence, created_at
                    ) VALUES (?, ?, ?, 'candidate_evidence', ?, ?)
                    ON CONFLICT(run_id, requirement_id, batch_number) DO UPDATE SET
                        evidence = excluded.evidence,
                        created_at = excluded.created_at
                    """,
                    (
                        batch.run_id,
                        requirement_id,
                        batch.number,
                        _bounded_excerpt(answer, 1_000),
                        now,
                    ),
                )

            for finding in _parse_structured_findings(answer, batch.paths):
                fingerprint = _finding_fingerprint(finding)
                self._connection.execute(
                    """
                    INSERT INTO audit_findings(
                        run_id, fingerprint, severity, path, line,
                        title, evidence, recommendation, status,
                        first_batch, last_batch, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
                    ON CONFLICT(run_id, fingerprint) DO UPDATE SET
                        severity = excluded.severity,
                        evidence = excluded.evidence,
                        recommendation = excluded.recommendation,
                        last_batch = excluded.last_batch,
                        updated_at = excluded.updated_at
                    """,
                    (
                        batch.run_id,
                        fingerprint,
                        finding["severity"],
                        finding["path"],
                        finding["line"],
                        finding["title"],
                        finding["evidence"],
                        finding["recommendation"],
                        batch.number,
                        batch.number,
                        now,
                        now,
                    ),
                )

    def list_findings(
        self,
        run_id: str,
        *,
        limit: int = 500,
    ) -> list[dict[str, object]]:
        """Return deduplicated structured findings for a run."""

        bounded = max(1, min(limit, 2_000))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT fingerprint, severity, path, line, title, evidence,
                       recommendation, status, first_batch, last_batch
                FROM audit_findings
                WHERE run_id = ?
                ORDER BY CASE severity
                    WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                    path, line
                LIMIT ?
                """,
                (run_id, bounded),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_requirements(self, run_id: str) -> list[dict[str, object]]:
        """Return the requirements matrix and evidence state."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT requirement_id, source, section, text, level, source_hash,
                       CASE WHEN EXISTS(
                           SELECT 1 FROM audit_requirement_evidence evidence
                           WHERE evidence.run_id = requirements.run_id
                             AND evidence.requirement_id = requirements.requirement_id
                       ) THEN 'candidate_evidence' ELSE 'not_proven' END AS status
                FROM audit_requirements requirements
                WHERE run_id = ?
                ORDER BY requirement_id
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def render_report(self, run_id: str, report_format: str = "text") -> str:
        """Render a complete report from SQLite without another model call."""

        if report_format not in {"text", "json"}:
            raise ValueError("report_format must be 'text' or 'json'")
        details = self.run_details(run_id)
        findings = self.list_findings(run_id, limit=2_000)
        requirements = self.list_requirements(run_id)
        with self._lock:
            batches = [
                dict(row)
                for row in self._connection.execute(
                    """
                    SELECT batch_number, paths, processed_count, answer, created_at
                    FROM audit_batches WHERE run_id = ? ORDER BY batch_number
                    """,
                    (run_id,),
                ).fetchall()
            ]
        payload = {
            "schema_version": 1,
            "run": details,
            "requirements": requirements,
            "findings": findings,
            "batches": batches,
        }
        if report_format == "json":
            return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        return _render_text_report(payload)

    def write_reports(
        self,
        run_id: str,
        report_file: Path,
        report_format: str,
    ) -> tuple[Path, ...]:
        """Write UTF-8 reports directly, bypassing PowerShell text pipelines."""

        if report_format not in {"text", "json", "both"}:
            raise ValueError("report_format must be text, json, or both")
        requested = report_file.expanduser().resolve()
        requested.parent.mkdir(parents=True, exist_ok=True)
        formats = ("text", "json") if report_format == "both" else (report_format,)
        written: list[Path] = []
        for item_format in formats:
            if len(formats) == 1:
                target = requested
            else:
                suffix = ".txt" if item_format == "text" else ".json"
                target = requested.with_suffix(suffix)
            target.write_text(
                self.render_report(run_id, item_format) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            written.append(target)
        return tuple(written)

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
                    mode TEXT NOT NULL DEFAULT 'read-only',
                    include_patterns TEXT NOT NULL DEFAULT '[]',
                    exclude_patterns TEXT NOT NULL DEFAULT '[]',
                    selected_count INTEGER NOT NULL DEFAULT 0,
                    excluded_count INTEGER NOT NULL DEFAULT 0,
                    exclusion_summary TEXT NOT NULL DEFAULT '{}',
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

                CREATE TABLE IF NOT EXISTS audit_requirements (
                    run_id TEXT NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
                    requirement_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    section TEXT NOT NULL,
                    text TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    PRIMARY KEY (run_id, requirement_id)
                );

                CREATE TABLE IF NOT EXISTS audit_requirement_evidence (
                    run_id TEXT NOT NULL,
                    requirement_id TEXT NOT NULL,
                    batch_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (run_id, requirement_id, batch_number),
                    FOREIGN KEY (run_id, requirement_id)
                        REFERENCES audit_requirements(run_id, requirement_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS audit_findings (
                    run_id TEXT NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    path TEXT NOT NULL,
                    line INTEGER,
                    title TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    first_batch INTEGER NOT NULL,
                    last_batch INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (run_id, fingerprint)
                );
                """
            )
            run_columns = {
                str(row[1])
                for row in self._connection.execute(
                    "PRAGMA table_info(audit_runs)"
                ).fetchall()
            }
            run_migrations = {
                "mode": "TEXT NOT NULL DEFAULT 'read-only'",
                "include_patterns": "TEXT NOT NULL DEFAULT '[]'",
                "exclude_patterns": "TEXT NOT NULL DEFAULT '[]'",
                "selected_count": "INTEGER NOT NULL DEFAULT 0",
                "excluded_count": "INTEGER NOT NULL DEFAULT 0",
                "exclusion_summary": "TEXT NOT NULL DEFAULT '{}'",
            }
            for column, declaration in run_migrations.items():
                if column not in run_columns:
                    self._connection.execute(
                        f"ALTER TABLE audit_runs ADD COLUMN {column} {declaration}"
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

    def _synchronize_files(
        self,
        run_id: str,
        workspace: Path,
        selection: AuditFileSelection,
    ) -> None:
        workspace_key = str(workspace)
        seen: set[str] = set()
        now = time.time()

        for path in selection.paths:
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
                """
                UPDATE audit_runs
                SET status = 'running', selected_count = ?, excluded_count = ?,
                    exclusion_summary = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    len(selection.paths),
                    selection.excluded,
                    json.dumps(selection.reasons, ensure_ascii=False, sort_keys=True),
                    now,
                    run_id,
                ),
            )
            self._set_completion_status(run_id)

    def _synchronize_requirements(
        self,
        run_id: str,
        workspace: Path,
        objective: str,
    ) -> None:
        requirements = extract_requirements(workspace, objective)
        with self._lock, self._connection:
            current_ids = {item["requirement_id"] for item in requirements}
            existing_ids = {
                str(row["requirement_id"])
                for row in self._connection.execute(
                    "SELECT requirement_id FROM audit_requirements WHERE run_id = ?",
                    (run_id,),
                ).fetchall()
            }
            self._connection.executemany(
                "DELETE FROM audit_requirements "
                "WHERE run_id = ? AND requirement_id = ?",
                ((run_id, item) for item in existing_ids - current_ids),
            )
            self._connection.executemany(
                """
                INSERT INTO audit_requirements(
                    run_id, requirement_id, source, section, text, level, source_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, requirement_id) DO UPDATE SET
                    source = excluded.source,
                    section = excluded.section,
                    text = excluded.text,
                    level = excluded.level,
                    source_hash = excluded.source_hash
                """,
                (
                    (
                        run_id,
                        item["requirement_id"],
                        item["source"],
                        item["section"],
                        item["text"],
                        item["level"],
                        item["source_hash"],
                    )
                    for item in requirements
                ),
            )

    def _set_completion_status(self, run_id: str) -> None:
        current = self._connection.execute(
            "SELECT status FROM audit_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if current is not None and str(current["status"]) in {"paused", "cancelled"}:
            return
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


def select_project_files(
    workspace: Path,
    rules: AuditSelectionRules | None = None,
) -> AuditFileSelection:
    """Build an exact deterministic file inventory with exclusion reasons."""

    selected_rules = rules or AuditSelectionRules()
    paths: list[Path] = []
    reasons: Counter[str] = Counter()

    def ignore_walk_error(_error: OSError) -> None:
        reasons["walk_error"] += 1
        return None

    for root, directory_names, file_names in os.walk(
        workspace,
        topdown=True,
        onerror=ignore_walk_error,
        followlinks=False,
    ):
        root_path = Path(root)
        kept_directories: list[str] = []
        for name in sorted(directory_names, key=str.casefold):
            child = root_path / name
            relative = child.relative_to(workspace).as_posix()
            if child.is_symlink():
                reasons["symlink_directory"] += 1
            elif _is_skipped_directory(name):
                reasons["generated_directory"] += 1
            elif _matches_any(relative, selected_rules.exclude):
                reasons["configured_exclude"] += 1
            else:
                kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(file_names, key=str.casefold):
            path = root_path / name
            relative = path.relative_to(workspace).as_posix()
            if path.is_symlink():
                reasons["symlink_file"] += 1
                continue
            if _is_skipped_file(name):
                reasons["secret_or_coverage"] += 1
                continue
            if selected_rules.include and not _matches_any(
                relative,
                selected_rules.include,
            ):
                reasons["not_included"] += 1
                continue
            if _matches_any(relative, selected_rules.exclude):
                reasons["configured_exclude"] += 1
                continue
            if path.suffix.casefold() in _BINARY_SUFFIXES:
                reasons["binary_suffix"] += 1
                continue
            try:
                with path.open("rb") as stream:
                    probe = stream.read(4_096)
                if b"\0" in probe:
                    reasons["binary_content"] += 1
                    continue
            except OSError:
                reasons["unreadable"] += 1
                continue
            paths.append(path)
    return AuditFileSelection(
        paths=tuple(paths),
        excluded=sum(reasons.values()),
        reasons=dict(sorted(reasons.items())),
    )


def _iter_project_files(workspace: Path) -> Iterator[Path]:
    """Yield default-selected files for backwards-compatible callers."""

    yield from select_project_files(workspace).paths


def _is_skipped_directory(name: str) -> bool:
    normalized = name.casefold()
    return (
        normalized in _SKIPPED_DIRECTORIES
        or normalized.startswith(_SKIPPED_DIRECTORY_PREFIXES)
        or normalized.endswith(_SKIPPED_DIRECTORY_SUFFIXES)
    )


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/").casefold()
    basename = normalized.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(normalized, pattern.replace("\\", "/").casefold())
        or (
            "/" not in pattern.replace("\\", "/")
            and fnmatch.fnmatchcase(basename, pattern.casefold())
        )
        for pattern in patterns
        if pattern.strip()
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


def extract_requirements(
    workspace: Path,
    objective: str,
) -> list[dict[str, str]]:
    """Extract stable requirement records from explicitly relevant Markdown."""

    candidates = {
        "TECHNICAL_SPEC.md",
        "TECHNICAL_SPECIFICATION.md",
    }
    for match in re.finditer(
        r"(?iu)(?:/workspace/)?([\w .()-]*(?:spec|тз)[\w .()-]*\.md)",
        objective,
    ):
        candidates.add(match.group(1).strip())

    requirements: dict[str, dict[str, str]] = {}
    for relative in sorted(candidates, key=str.casefold):
        candidate = (workspace / relative).resolve()
        try:
            if (
                not candidate.is_relative_to(workspace.resolve())
                or not candidate.is_file()
            ):
                continue
            raw = candidate.read_bytes()
        except OSError:
            continue
        if len(raw) > 4 * 1024 * 1024:
            raw = raw[: 4 * 1024 * 1024]
        text = _decode_text(raw)
        source_hash = hashlib.sha256(raw).hexdigest()
        section = "Document"
        source = candidate.relative_to(workspace).as_posix()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                section = stripped.lstrip("#").strip()[:300] or "Document"
                continue
            line_match = _REQUIREMENT_LINE_PATTERN.match(line)
            if line_match is None:
                continue
            requirement_text = line_match.group("text").strip()
            if not _REQUIREMENT_TERM_PATTERN.search(requirement_text):
                continue
            fingerprint = "\0".join((source, section, requirement_text))
            requirement_id = (
                "REQ-"
                + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:10].upper()
            )
            requirements[requirement_id] = {
                "requirement_id": requirement_id,
                "source": source,
                "section": section,
                "text": requirement_text[:2_000],
                "level": (
                    "must"
                    if _REQUIREMENT_TERM_PATTERN.search(requirement_text)
                    else "informative"
                ),
                "source_hash": source_hash,
            }
    return [requirements[key] for key in sorted(requirements)]


def _parse_structured_findings(
    answer: str,
    batch_paths: tuple[str, ...],
) -> list[dict[str, object]]:
    match = _AUDIT_FINDINGS_PATTERN.search(answer)
    if match is None:
        return []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    allowed_paths = {path.casefold(): path for path in batch_paths}
    findings: list[dict[str, object]] = []
    for raw in payload[:100]:
        if not isinstance(raw, Mapping):
            continue
        path = _normalize_project_path(str(raw.get("path", "")))
        if path.casefold() not in allowed_paths:
            continue
        severity = str(raw.get("severity", "info")).casefold()
        if severity not in _FINDING_SEVERITIES:
            severity = "info"
        line_value = raw.get("line")
        line = line_value if isinstance(line_value, int) and line_value > 0 else None
        title = str(raw.get("title", "")).strip()[:500]
        evidence = str(raw.get("evidence", "")).strip()[:2_000]
        recommendation = str(raw.get("recommendation", "")).strip()[:2_000]
        if not title or not evidence:
            continue
        findings.append(
            {
                "severity": severity,
                "path": allowed_paths[path.casefold()],
                "line": line,
                "title": title,
                "evidence": evidence,
                "recommendation": recommendation,
            }
        )
    return findings


def _finding_fingerprint(finding: Mapping[str, object]) -> str:
    identity = "\0".join(
        (
            str(finding["path"]).casefold(),
            str(finding.get("line") or ""),
            str(finding["title"]).casefold(),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _bounded_excerpt(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _render_text_report(payload: Mapping[str, object]) -> str:
    run = payload["run"]
    if not isinstance(run, Mapping):
        raise ValueError("Invalid report payload")
    progress = run.get("progress", {})
    lines = [
        "Deep Context Agent project audit report",
        f"run_id: {run.get('id')}",
        f"status: {run.get('status')}",
        f"mode: {run.get('mode')}",
        f"workspace: {run.get('workspace')}",
        f"objective: {run.get('objective')}",
        f"progress: {json.dumps(progress, ensure_ascii=False, sort_keys=True)}",
        "",
        "Requirements matrix",
    ]
    requirements = payload.get("requirements", [])
    if isinstance(requirements, list):
        for item in requirements:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                f"- {item.get('requirement_id')} [{item.get('status')}] "
                f"{item.get('source')} — {item.get('text')}"
            )
    lines.extend(("", "Structured findings"))
    findings = payload.get("findings", [])
    if isinstance(findings, list) and findings:
        for item in findings:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                f"- [{item.get('severity')}] {item.get('path')}:"
                f"{item.get('line') or '-'} {item.get('title')} — "
                f"{item.get('evidence')} Recommendation: "
                f"{item.get('recommendation')}"
            )
    else:
        lines.append("- No structured findings were recorded.")
    lines.extend(("", "Batch evidence"))
    batches = payload.get("batches", [])
    if isinstance(batches, list):
        for batch in batches:
            if not isinstance(batch, Mapping):
                continue
            lines.extend(
                (
                    f"\n## Batch {batch.get('batch_number')}",
                    f"paths:\n{batch.get('paths')}",
                    f"processed_count: {batch.get('processed_count')}",
                    str(batch.get("answer", "")),
                )
            )
    return "\n".join(lines)
