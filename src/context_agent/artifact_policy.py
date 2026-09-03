"""Shared artifact exclusion and bounded workspace traversal policy."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

DEFAULT_EXCLUDED_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {
        ".agent_data",
        ".cache",
        ".deps",
        ".diagnostic-exports",
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
DEFAULT_EXCLUDED_DIRECTORY_PREFIXES: Final[tuple[str, ...]] = (
    ".pytest-",
    ".pytest_",
    "browser-profile",
    "chrome-profile",
    "edge-profile",
)
DEFAULT_EXCLUDED_DIRECTORY_SUFFIXES: Final[tuple[str, ...]] = (".egg-info",)
DEFAULT_EXCLUDED_FILES: Final[frozenset[str]] = frozenset(
    {
        ".coverage",
        ".env",
        ".env.local",
        ".env.production",
        ".env.test",
        "context-agent-server.jsonl",
        "diagnostics.sqlite3",
        "diagnostics.sqlite3-shm",
        "diagnostics.sqlite3-wal",
    }
)
DEFAULT_EXCLUDED_FILE_PREFIXES: Final[tuple[str, ...]] = (
    ".coverage.",
    ".env.",
    "context-agent-server.jsonl.",
    "diagnostic-export",
)
BINARY_SUFFIXES: Final[frozenset[str]] = frozenset(
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
TEXT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "",
        ".cfg",
        ".csv",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsonl",
        ".jsx",
        ".log",
        ".md",
        ".mjs",
        ".py",
        ".rst",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".tsv",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)


@dataclass(frozen=True, slots=True)
class ArtifactPolicy:
    """One policy shared by discovery, content search, indexing, and audits."""

    excluded_directories: frozenset[str] = DEFAULT_EXCLUDED_DIRECTORIES
    excluded_directory_prefixes: tuple[str, ...] = DEFAULT_EXCLUDED_DIRECTORY_PREFIXES
    excluded_directory_suffixes: tuple[str, ...] = DEFAULT_EXCLUDED_DIRECTORY_SUFFIXES
    excluded_files: frozenset[str] = DEFAULT_EXCLUDED_FILES
    excluded_file_prefixes: tuple[str, ...] = DEFAULT_EXCLUDED_FILE_PREFIXES
    binary_suffixes: frozenset[str] = BINARY_SUFFIXES
    text_suffixes: frozenset[str] = TEXT_SUFFIXES

    def directory_reason(self, name: str) -> str | None:
        """Return the shared exclusion reason for a directory name."""

        normalized = name.casefold()
        if (
            normalized in self.excluded_directories
            or normalized.startswith(self.excluded_directory_prefixes)
            or normalized.endswith(self.excluded_directory_suffixes)
        ):
            return "generated_or_private_directory"
        return None

    def file_reason(self, path: Path, *, text_only: bool = False) -> str | None:
        """Return the shared exclusion reason for a file path."""

        normalized = path.name.casefold()
        if normalized in self.excluded_files or normalized.startswith(
            self.excluded_file_prefixes
        ):
            return "secret_or_agent_artifact"
        suffix = path.suffix.casefold()
        if suffix in self.binary_suffixes:
            return "binary_suffix"
        if text_only and suffix not in self.text_suffixes:
            return "unsupported_text_suffix"
        return None

    def path_reason(
        self,
        path: Path,
        root: Path,
        *,
        text_only: bool = False,
    ) -> str | None:
        """Return why a path is excluded, including all parent directories."""

        try:
            relative = path.relative_to(root)
        except ValueError:
            return "outside_workspace"
        if any(self.directory_reason(part) for part in relative.parts[:-1]):
            return "generated_or_private_directory"
        return self.file_reason(path, text_only=text_only)


DEFAULT_ARTIFACT_POLICY: Final[ArtifactPolicy] = ArtifactPolicy()


@dataclass(frozen=True, slots=True)
class TraversalPage:
    """A deterministic bounded page from a workspace traversal."""

    paths: tuple[Path, ...]
    next_cursor: str | None
    complete: bool
    scanned: int
    excluded: int
    reasons: dict[str, int] = field(default_factory=dict)


def encode_cursor(offset: int, *, scope: str = "") -> str:
    """Encode a non-negative traversal offset as an opaque cursor."""

    checksum = hashlib.sha256(f"1:{offset}:{scope}:dca-cursor".encode()).hexdigest()[
        :16
    ]
    payload = json.dumps(
        {"v": 1, "offset": offset, "scope": scope, "check": checksum},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, *, scope: str = "") -> int:
    """Decode and validate an opaque traversal cursor."""

    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        offset = int(payload["offset"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid traversal cursor") from exc
    cursor_scope = payload.get("scope")
    checksum = hashlib.sha256(
        f"1:{offset}:{cursor_scope}:dca-cursor".encode()
    ).hexdigest()[:16]
    if (
        payload.get("v") != 1
        or offset < 0
        or cursor_scope != scope
        or payload.get("check") != checksum
    ):
        raise ValueError("Invalid traversal cursor")
    return offset


def _cursor_scope(root: Path, pattern: str, text_only: bool) -> str:
    canonical = json.dumps(
        {
            "root": str(root.resolve()).casefold(),
            "pattern": pattern,
            "text_only": text_only,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:20]


def iter_workspace_files(
    root: Path,
    *,
    policy: ArtifactPolicy = DEFAULT_ARTIFACT_POLICY,
    text_only: bool = False,
) -> Iterator[tuple[Path, str | None]]:
    """Yield deterministic files and excluded entries without retaining a tree."""

    def ignore_walk_error(_error: OSError) -> None:
        return None

    for current, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=ignore_walk_error,
        followlinks=False,
    ):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directory_names, key=str.casefold):
            child = current_path / name
            reason = (
                "symlink_directory"
                if child.is_symlink()
                else policy.directory_reason(name)
            )
            if reason:
                yield child, reason
            else:
                kept.append(name)
        directory_names[:] = kept
        for name in sorted(file_names, key=str.casefold):
            path = current_path / name
            reason = (
                "symlink_file"
                if path.is_symlink()
                else policy.file_reason(path, text_only=text_only)
            )
            yield path, reason


def scan_workspace_page(
    root: Path,
    *,
    pattern: str = "**/*",
    cursor: str = "",
    page_size: int = 200,
    policy: ArtifactPolicy = DEFAULT_ARTIFACT_POLICY,
    text_only: bool = False,
) -> TraversalPage:
    """Return one page of matching files with an opaque continuation cursor."""

    if not 1 <= page_size <= 1_000:
        raise ValueError("page_size must be between 1 and 1000")
    normalized_pattern = pattern.replace("\\", "/") or "**/*"
    scope = _cursor_scope(root, normalized_pattern, text_only)
    offset = decode_cursor(cursor, scope=scope)
    scanned = 0
    excluded = 0
    reasons: Counter[str] = Counter()
    page: list[Path] = []
    has_more = False
    next_offset: int | None = None
    scan_budget = min(max(page_size * 20, page_size), 20_000)
    for raw_index, (path, reason) in enumerate(
        iter_workspace_files(root, policy=policy, text_only=text_only)
    ):
        if raw_index < offset:
            continue
        if scanned >= scan_budget:
            has_more = True
            next_offset = raw_index
            break
        scanned += 1
        if reason:
            excluded += 1
            reasons[reason] += 1
            continue
        relative = path.relative_to(root).as_posix()
        basename = path.name
        root_compatible_pattern = (
            normalized_pattern.removeprefix("**/")
            if normalized_pattern.startswith("**/")
            else normalized_pattern
        )
        if not (
            fnmatch.fnmatchcase(relative, normalized_pattern)
            or fnmatch.fnmatchcase(basename, normalized_pattern)
            or fnmatch.fnmatchcase(relative, root_compatible_pattern)
        ):
            continue
        page.append(path)
        if len(page) == page_size:
            has_more = True
            next_offset = raw_index + 1
            break
    next_cursor = (
        encode_cursor(next_offset, scope=scope)
        if has_more and next_offset is not None
        else None
    )
    return TraversalPage(
        paths=tuple(page),
        next_cursor=next_cursor,
        complete=not has_more,
        scanned=scanned,
        excluded=excluded,
        reasons=dict(sorted(reasons.items())),
    )
