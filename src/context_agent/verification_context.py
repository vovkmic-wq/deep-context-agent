"""Bounded, runtime-owned evidence about the code and interpreter being checked."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import ConfigDict, TypeAdapter, ValidationError

from context_agent.artifact_policy import DEFAULT_ARTIFACT_POLICY
from context_agent.checked_process import run_checked_process


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True, slots=True)
class VerificationInvocation:
    """Server-supplied attribution; a snapshot never grants future permissions."""

    __pydantic_config__ = ConfigDict(extra="forbid", strict=True)
    task_id: str | None = None
    job_id: str | None = None
    request_id: str | None = None
    task_revision: int | None = None
    job_generation: int | None = None
    attempt_id: str = field(default_factory=lambda: uuid4().hex)
    source: Literal["runner", "runtime"] = "runner"
    read_allowed: bool = True
    scan_allowed: bool = False
    checks_allowed: bool = True
    write_allowed: bool = False

    def __post_init__(self) -> None:
        for value in (self.task_id, self.job_id, self.request_id, self.attempt_id):
            if value is not None and (not value or len(value) > 200):
                raise ValueError("Invalid verification invocation identity")
        if any(
            value is not None and value < 0
            for value in (self.task_revision, self.job_generation)
        ):
            raise ValueError("Invalid verification invocation revision")


@dataclass(frozen=True, slots=True)
class VerificationContext:
    """Immutable schema 2. Private provenance is stored once, never in prompts."""

    __pydantic_config__ = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    schema_version: Literal[2]
    run_id: str
    invocation: VerificationInvocation
    project_root: str
    root_source: Literal["explicit", "discovery", "saved_context"]
    parent_context_id: str | None
    scope_root: str
    source_sha256: str
    code_revision: str
    configuration_hashes: tuple[tuple[str, str], ...]
    launch_reference: str
    prefix: str
    base_prefix: str
    environment_label: str
    python_version: str
    environment_sha256: str
    dependencies_sha256: str
    dependency_versions: tuple[tuple[str, str], ...]
    tool_versions: tuple[tuple[str, str | None], ...]
    package_origins: tuple[tuple[str, tuple[str, ...]], ...]
    origin_status: str
    checks: tuple[str, ...]
    verification_scope: Literal["partial", "project"]
    captured_at: float

    def __post_init__(self) -> None:
        for digest in (
            self.source_sha256,
            self.environment_sha256,
            self.dependencies_sha256,
        ):
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Invalid verification fingerprint")
        if not self.run_id or not self.checks or self.captured_at <= 0:
            raise ValueError("Incomplete verification context")
        if (
            self.scope_root != "/workspace"
            or not (
                self.project_root == "/workspace"
                or self.project_root.startswith("/workspace/")
            )
            or any(p in {".", ".."} for p in self.project_root.split("/"))
        ):
            raise ValueError("Invalid verification scope")

    def payload(self) -> dict[str, Any]:
        return json.loads(_canonical(asdict(self)))

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> VerificationContext:
        try:
            return TypeAdapter(cls).validate_json(_canonical(payload))
        except (ValidationError, TypeError, ValueError) as exc:
            raise ValueError("Invalid verification context schema") from exc

    @property
    def context_id(self) -> str:
        return hashlib.sha256(_canonical(self.payload()).encode()).hexdigest()

    def receipt(self) -> dict[str, Any]:
        """Safe references only; private paths/versions remain in the store."""
        return {
            "schema_version": 2,
            "context_id": self.context_id,
            "run_id": self.run_id,
            "attempt_id": self.invocation.attempt_id,
            "project_root": self.project_root,
            "environment_label": self.environment_label,
            "python_version": self.python_version,
            "source_sha256": self.source_sha256,
            "environment_sha256": self.environment_sha256,
            "checks": list(self.checks),
            "verification_scope": self.verification_scope,
        }


def capture_context(
    root: Path,
    workspace: Path,
    python: str,
    probe: dict[str, Any],
    checks: tuple[str, ...],
    fingerprint: str,
    *,
    invocation: VerificationInvocation,
    root_source: str,
    partial: bool,
    parent_context_id: str | None = None,
) -> VerificationContext:
    """Capture using trusted runtime arguments, not fields supplied by a model."""
    if not isinstance(probe.get("dependencies_sha256"), str):
        raise ValueError("Project environment dependency fingerprint unavailable")
    snapshot_hash, configuration_hashes = source_snapshot(root)
    if snapshot_hash != fingerprint:
        raise ValueError("Verification source changed during context capture")
    metadata = context_metadata(root, workspace, python, probe, checks, fingerprint)
    origins = probe.get("origins", {})
    payload = {
        **metadata,
        "schema_version": 2,
        "run_id": uuid4().hex,
        "invocation": asdict(invocation),
        "root_source": root_source,
        "parent_context_id": parent_context_id,
        "scope_root": "/workspace",
        "code_revision": "sha256:" + fingerprint,
        "configuration_hashes": configuration_hashes,
        "launch_reference": python,
        "prefix": probe["prefix"],
        "base_prefix": probe["base_prefix"],
        "dependencies_sha256": probe["dependencies_sha256"],
        "dependency_versions": sorted(probe.get("dependencies", [])),
        "tool_versions": sorted(probe.get("tool_versions", {}).items()),
        "package_origins": sorted(
            (name, [origin] if isinstance(origin, str) else origin or [])
            for name, origin in origins.items()
        ),
        "origin_status": probe.get("origin_status", "unavailable"),
        "verification_scope": "partial" if partial else "project",
        "captured_at": time.time(),
    }
    del payload["context_id"]
    return VerificationContext.from_payload(payload)


class VerificationContextStore:
    """Append-only content-addressed evidence, scoped to its actual workspace."""

    def __init__(self, database: Path) -> None:
        self.connection = sqlite3.connect(database, timeout=5)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS verification_contexts ("
            "workspace TEXT NOT NULL, context_id TEXT NOT NULL, "
            "payload TEXT NOT NULL, created_at REAL NOT NULL, "
            "PRIMARY KEY(workspace, context_id))"
        )
        self.connection.commit()

    def __enter__(self) -> VerificationContextStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.connection.close()

    def save(self, workspace: Path, payload: dict[str, Any]) -> str:
        self._validate_schema(payload)
        encoded = _canonical(payload)
        if len(encoded.encode("utf-8")) > 128_000:
            raise ValueError("Verification context exceeds size limit")
        identity = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO verification_contexts VALUES (?, ?, ?, ?)",
                (str(workspace.resolve()), identity, encoded, time.time()),
            )
        self.load(workspace, identity)
        return identity

    def load(self, workspace: Path, identity: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT payload FROM verification_contexts "
            "WHERE workspace=? AND context_id=?",
            (str(workspace.resolve()), identity),
        ).fetchone()
        if row is None:
            raise ValueError("Verification context unavailable for this workspace")
        encoded = str(row[0])
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != identity:
            raise ValueError("Verification context integrity failure")
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise ValueError("Verification context integrity failure")
        self._validate_schema(payload)
        return payload

    @staticmethod
    def _validate_schema(payload: dict[str, Any]) -> None:
        version = payload.get("schema_version", 1)
        if version == 2:
            VerificationContext.from_payload(payload)
        elif version != 1:
            raise ValueError("Unsupported verification context schema")


_PROBE = """
import hashlib, importlib.metadata, importlib.util, json, sys
packages = []
for distribution in importlib.metadata.distributions():
    if len(packages) >= 2000:
        raise RuntimeError('Environment metadata budget exceeded')
    packages.append((distribution.metadata.get('Name', ''), distribution.version))
encoded = json.dumps(sorted(packages), ensure_ascii=True)
origins = {}
for name in json.loads(sys.argv[1]):
    spec = importlib.util.find_spec(name)
    origins[name] = ([spec.origin] if spec and spec.origin else
                     list(spec.submodule_search_locations or []) if spec else [])
versions = {name.lower(): version for name, version in packages}
print(json.dumps({
    'version': sys.version.split()[0],
    'prefix': sys.prefix, 'base_prefix': sys.base_prefix,
    'tools': {n: bool(importlib.util.find_spec(n)) for n in ('ruff','pytest','mypy')},
    'tool_versions': {n: versions.get(n) for n in ('ruff','pytest','mypy')},
    'dependencies_sha256': hashlib.sha256(encoded.encode()).hexdigest(),
    'dependencies': sorted(packages),
    'origins': origins,
}, ensure_ascii=True))
"""
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


def _package_roots(root: Path) -> tuple[Path, ...]:
    """Support conventional src/flat and explicit setuptools/Poetry layouts."""
    manifest = root / "pyproject.toml"
    roots: set[Path] = set()
    if manifest.is_file():
        if manifest.stat().st_size > 1_000_000:
            raise ValueError("Project package manifest budget exceeded")
        tool = tomllib.loads(manifest.read_text(encoding="utf-8")).get("tool", {})
        if not isinstance(tool, dict):
            raise ValueError("Project package configuration is invalid")
        setuptools = tool.get("setuptools", {})
        if not isinstance(setuptools, dict):
            raise ValueError("Project package setuptools configuration is invalid")
        if not isinstance(setuptools.get("package-dir", {}), dict):
            raise ValueError("Project package directory mapping is invalid")
        for value in setuptools.get("package-dir", {}).values():
            roots.add(root / str(value))
        packages = setuptools.get("packages", {})
        if isinstance(packages, dict):
            find = packages.get("find", {})
            if not isinstance(find, dict) or not isinstance(
                find.get("where", []), list
            ):
                raise ValueError("Project package discovery configuration is invalid")
            for value in find.get("where", []):
                roots.add(root / str(value))
        poetry = tool.get("poetry", {})
        if not isinstance(poetry, dict) or not isinstance(
            poetry.get("packages", []), list
        ):
            raise ValueError("Project package Poetry configuration is invalid")
        for package in poetry.get("packages", []):
            if isinstance(package, dict):
                roots.add(root / str(package.get("from", ".")))
    if not roots:
        roots.add(root / "src" if (root / "src").is_dir() else root)
    if len(roots) > 30:
        raise ValueError("Project package layout budget exceeded")
    for package_root in roots:
        if not package_root.resolve().is_relative_to(root.resolve()):
            raise ValueError("Project package discovery escapes selected root")
        if not package_root.is_dir():
            raise ValueError("Project package directory unavailable")
    return tuple(sorted(roots))


def probe_environment(
    python: str,
    root: Path,
    environment: dict[str, str],
    timeout: float,
    check_authority: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Inspect the selected interpreter, never the agent process interpreter."""
    package_names: set[str] = set()
    count = 0
    for package_root in _package_roots(root):
        with os.scandir(package_root) as entries:
            for entry in entries:
                count += 1
                if count > 1000:
                    raise ValueError("Project package discovery budget exceeded")
                if (
                    entry.name.isidentifier()
                    and not entry.is_symlink()
                    and entry.is_dir(follow_symlinks=False)
                    and entry.name
                    not in {"tests", "docs", "scripts", "config", "webui"}
                    and DEFAULT_ARTIFACT_POLICY.directory_reason(entry.name) is None
                    and (
                        package_root != root
                        or (Path(entry.path) / "__init__.py").is_file()
                    )
                ):
                    # src/layout directories also contain PEP 420 namespaces.
                    package_names.add(entry.name)
    result = run_checked_process(
        [python, "-c", _PROBE, json.dumps(sorted(package_names))],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        shell=False,
        check=False,
        timeout=min(timeout, 10),
        check_authority=check_authority,
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
    origins = data.get("origins", {})
    if not isinstance(origins, dict):
        raise ValueError("Project package origin metadata invalid")
    for name in package_names:
        origin = origins.get(name)
        locations = [origin] if isinstance(origin, str) else origin
        if not isinstance(locations, list) or not locations:
            raise ValueError("Project package origin unavailable: " + name)
        if any(
            not isinstance(path, str)
            or not Path(path).resolve().is_relative_to(root.resolve())
            for path in locations
        ):
            raise ValueError(
                "Project package origin is outside selected project: " + name
            )
    data["origin_status"] = "verified" if package_names else "no_packages_discovered"
    return data


def source_fingerprint(root: Path, *, max_entries: int = 50_000) -> str:
    """Hash relevant sources/configuration without following directory links."""
    return source_snapshot(root, max_entries=max_entries)[0]


def source_snapshot(
    root: Path, *, max_entries: int = 50_000
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Bounded snapshot includes individual configuration/lock fingerprints."""
    root = root.resolve()
    digest = hashlib.sha256()
    started = time.monotonic()
    count = 0
    byte_count = 0
    pending = [root]
    visited: set[Path] = set()
    files: list[Path] = []
    configuration: list[tuple[str, str]] = []
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
                    (
                        path.suffix in _SOURCE_SUFFIXES
                        or path.name in {"uv.lock", "poetry.lock", "Pipfile.lock"}
                        or path.match("requirements*.txt")
                    )
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
        if path.suffix in {".toml", ".ini", ".cfg", ".yaml", ".yml", ".json"} or (
            path.name in {"uv.lock", "poetry.lock", "Pipfile.lock"}
            or path.match("requirements*.txt")
        ):
            if len(configuration) >= 1000:
                raise ValueError("Verification source configuration limit exceeded")
            configuration.append(
                (path.relative_to(root).as_posix(), content_digest.hexdigest())
            )
    return digest.hexdigest(), tuple(configuration)


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
