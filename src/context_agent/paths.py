"""Safe path resolution for user and model supplied paths."""

from __future__ import annotations

from pathlib import Path

from context_agent.errors import PathSecurityError


def strip_workspace_prefix(value: str | Path) -> Path:
    """Convert a Deep Agents `/workspace` path to a relative local path."""
    normalized = str(value).replace("\\", "/")
    if normalized in {"/workspace", "/workspace/"}:
        return Path(".")
    if normalized.startswith("/workspace/"):
        return Path(normalized.removeprefix("/workspace/"))
    return Path(value)


def resolve_inside(
    root: Path,
    requested: str | Path,
    *,
    must_exist: bool = False,
    allow_root: bool = True,
) -> Path:
    """Resolve a path and reject traversal or symlink escape from ``root``."""
    resolved_root = root.expanduser().resolve(strict=True)
    relative_or_absolute = strip_workspace_prefix(requested)
    candidate = (
        relative_or_absolute
        if relative_or_absolute.is_absolute()
        else resolved_root / relative_or_absolute
    )
    try:
        resolved = candidate.expanduser().resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise PathSecurityError(f"Cannot resolve path: {requested}") from exc

    if not resolved.is_relative_to(resolved_root):
        raise PathSecurityError(
            f"Path '{requested}' is outside the allowed root '{resolved_root}'"
        )
    if not allow_root and resolved == resolved_root:
        raise PathSecurityError("The allowed root itself cannot be modified")
    return resolved
