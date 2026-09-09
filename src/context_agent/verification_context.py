"""Bounded, runtime-owned evidence about the code and interpreter being checked."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from context_agent.artifact_policy import DEFAULT_ARTIFACT_POLICY

_PROBE = (
    "import importlib.util,json,sys; "
    "print(json.dumps({'version':sys.version.split()[0],"
    "'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
    "'tools':{n:bool(importlib.util.find_spec(n)) "
    "for n in ('ruff','pytest','mypy')}},ensure_ascii=True))"
)
_SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".toml",
        ".ini",
        ".cfg",
        ".js",
        ".mjs",
        ".ts",
        ".tsx",
        ".html",
        ".css",
        ".json",
        ".yaml",
        ".yml",
    }
)


def probe_environment(
    python: str, root: Path, environment: dict[str, str], timeout: float
) -> dict[str, Any]:
    """Inspect the selected interpreter, never the agent process interpreter."""
    result = subprocess.run(
        [python, "-c", _PROBE],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        shell=False,
        check=False,
        timeout=min(timeout, 10),
    )
    if result.returncode or len(result.stdout) > 16_000:
        raise ValueError("Project environment preflight failed")
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("tools"), dict):
        raise ValueError("Project environment returned invalid metadata")
    if any(
        not isinstance(data.get(key), str)
        for key in ("version", "prefix", "base_prefix")
    ):
        raise ValueError("Project environment identity is unavailable")
    return data


def source_fingerprint(root: Path, *, max_entries: int = 50_000) -> str:
    """Hash relevant sources/configuration without following directory links."""
    digest = hashlib.sha256()
    started = time.monotonic()
    count = 0
    byte_count = 0
    pending = [root]
    visited: set[Path] = set()
    files: list[Path] = []
    while pending:
        directory = pending.pop()
        resolved = directory.resolve()
        if not resolved.is_relative_to(root) or resolved in visited:
            continue
        visited.add(resolved)
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > max_entries or time.monotonic() - started > 10:
                    raise ValueError("Verification source snapshot budget exceeded")
                if entry.is_symlink():
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if DEFAULT_ARTIFACT_POLICY.directory_reason(entry.name) is None:
                        pending.append(path)
                elif (
                    path.suffix in _SOURCE_SUFFIXES
                    and entry.is_file()
                    and DEFAULT_ARTIFACT_POLICY.file_reason(path) is None
                ):
                    files.append(path)
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        content_digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                byte_count += len(chunk)
                if byte_count > 256 * 1024 * 1024 or time.monotonic() - started > 10:
                    raise ValueError("Verification source snapshot budget exceeded")
                content_digest.update(chunk)
        digest.update(content_digest.digest())
    return digest.hexdigest()


def context_metadata(
    root: Path,
    workspace: Path,
    python: str,
    probe: dict[str, Any],
    checks: tuple[str, ...],
    fingerprint: str,
) -> dict[str, Any]:
    """Expose hashes and virtual paths, not host paths or process credentials."""
    relative = root.relative_to(workspace).as_posix()
    identity = json.dumps({"python": python, **probe}, sort_keys=True)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "project_root": "/workspace" + ("/" + relative if relative != "." else ""),
        "environment_label": ".venv" if ".venv" in Path(python).parts else "configured",
        "python_version": probe["version"],
        "environment_sha256": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "source_sha256": fingerprint,
        "checks": list(checks),
    }
    payload["context_id"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload
