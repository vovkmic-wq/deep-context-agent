"""Durable bounded discovery using the shared artifact/confinement policy.

Directory offsets are valid only while the directory identity/mtime is unchanged.
Python has no portable durable scandir handle: replay within one directory is
time-bounded too. No OS iterator or thousands of paths are returned to the LLM.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from context_agent.artifact_policy import DEFAULT_ARTIFACT_POLICY, TraversalPage
from context_agent.paths import resolve_inside


def _signature(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_mtime_ns]


class DurableManifestScan:
    """Checkpoint a frontier atomically; never accept a partial unique result."""

    def __init__(self, database: Path | None) -> None:
        self.db = sqlite3.connect(str(database) if database else ":memory:", timeout=1)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS root_discovery ("
            "workspace TEXT PRIMARY KEY, cursor TEXT NOT NULL, "
            "active INTEGER NOT NULL, payload TEXT NOT NULL)"
        )
        self.db.commit()

    def __enter__(self) -> DurableManifestScan:
        return self

    def __exit__(self, *_: object) -> None:
        self.db.close()

    def page(
        self,
        workspace: Path,
        *,
        max_entries: int,
        timeout: float,
        max_matches: int,
        check_authority: Callable[[], None] = lambda: None,
    ) -> TraversalPage:
        if not 1 <= max_entries <= 100_000 or not 0 < timeout <= 60:
            raise ValueError("Invalid root scan page limits")
        root = workspace.resolve()
        started = time.monotonic()
        previous = self.db.execute(
            "SELECT cursor, payload, active FROM root_discovery WHERE workspace=?",
            (str(root),),
        ).fetchone()
        row = previous if previous and previous[2] else None
        cursor = str(row[0]) if row else uuid4().hex
        state: dict[str, Any] = (
            json.loads(row[1])
            if row
            else {
                "pending": [[".", 0, None]],
                "visited": [],
                "matches": [],
                "scanned": 0,
                "excluded": 0,
                "validation": 0,
            }
        )
        processed = 0
        try:
            while processed < max_entries and time.monotonic() - started < timeout:
                check_authority()
                if not state["pending"]:
                    index = state["validation"]
                    if index == len(state["visited"]):
                        break
                    relative, signature = state["visited"][index]
                    path = resolve_inside(
                        root, relative, allow_root=True, must_exist=True
                    )
                    if _signature(path) != signature:
                        raise ValueError("Project-root scan changed; restart discovery")
                    state["validation"] += 1
                    processed += 1
                    continue
                item = state["pending"][-1]
                relative, offset, signature = item
                directory = resolve_inside(
                    root, relative, allow_root=True, must_exist=True
                )
                if signature is None:
                    signature = item[2] = _signature(directory)
                if signature != _signature(directory):
                    raise ValueError("Project-root scan changed; restart discovery")
                children = []
                exhausted = True
                with os.scandir(directory) as entries:
                    for index, entry in enumerate(entries):
                        if time.monotonic() - started >= timeout:
                            exhausted = False
                            break
                        if index < offset:
                            continue
                        if processed >= max_entries:
                            exhausted = False
                            break
                        check_authority()
                        item[1] = index + 1
                        processed += 1
                        state["scanned"] += 1
                        if state["scanned"] > 1_000_000:
                            raise ValueError("Project-root scan total budget exceeded")
                        path = Path(entry.path)
                        if entry.is_symlink() or (
                            hasattr(path, "is_junction") and path.is_junction()
                        ):
                            state["excluded"] += 1
                        elif entry.is_dir(follow_symlinks=False):
                            if DEFAULT_ARTIFACT_POLICY.directory_reason(entry.name):
                                state["excluded"] += 1
                            else:
                                children.append(
                                    [path.relative_to(root).as_posix(), 0, None]
                                )
                        elif entry.name == "pyproject.toml" and entry.is_file():
                            resolve_inside(root, str(path), must_exist=True)
                            state["matches"].append(path.relative_to(root).as_posix())
                            if len(state["matches"]) > max_matches:
                                raise ValueError("Project-root manifest limit exceeded")
                if signature != _signature(directory):
                    raise ValueError("Project-root scan changed; restart discovery")
                if exhausted:
                    state["pending"].pop()
                    state["visited"].append([relative, signature])
                    processed += 1  # Empty directories consume the page budget too.
                state["pending"].extend(children)
            complete = not state["pending"] and (
                state["validation"] == len(state["visited"])
            )
            if not complete and processed == 0:
                raise ValueError(
                    "Project-root resume budget exceeded; supply exact root"
                )
            payload = json.dumps(state, separators=(",", ":"))
            if len(payload) > 2_000_000:
                raise ValueError("Project-root checkpoint budget exceeded")
            # Do not hold a SQLite writer lock during filesystem IO/heartbeats.
            # Concurrent scanners may duplicate a read, never overwrite a new cursor.
            if previous:
                changed = self.db.execute(
                    "UPDATE root_discovery SET cursor=?, active=?, payload=? "
                    "WHERE workspace=? AND cursor=? AND payload=? AND active=?",
                    (cursor, int(not complete), payload, str(root), *previous),
                )
            else:
                changed = self.db.execute(
                    "INSERT OR IGNORE INTO root_discovery VALUES (?, ?, ?, ?)",
                    (str(root), cursor, int(not complete), payload),
                )
            if changed.rowcount != 1:
                raise ValueError("Concurrent root scan changed; resume latest cursor")
            self.db.commit()
        except BaseException as exc:
            self.db.rollback()
            # A changed/unsafe snapshot cannot be resumed as if still current.
            # Cancellation keeps the last committed frontier; no progress is invented.
            if previous and isinstance(exc, (OSError, ValueError)):
                with self.db:
                    self.db.execute(
                        "UPDATE root_discovery SET active=0 "
                        "WHERE workspace=? AND cursor=? AND payload=? AND active=?",
                        (str(root), *previous),
                    )
            raise
        return TraversalPage(
            paths=tuple(root / relative for relative in state["matches"]),
            next_cursor=None if complete else cursor,
            complete=complete,
            scanned=state["scanned"],
            excluded=state["excluded"],
        )
