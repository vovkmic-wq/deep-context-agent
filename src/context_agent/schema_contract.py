"""Build a bounded, runtime-owned contract snapshot before implementation."""

from __future__ import annotations

import ast
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".agent_data",
    }
)
_CONTRACT_STEMS = frozenset(
    {"schema", "schemas", "model", "models", "types", "entities", "dto", "contracts"}
)


@dataclass(frozen=True, slots=True)
class SchemaContract:
    """Compact public names and signatures extracted without model inference."""

    files_scanned: int
    truncated: bool
    symbols: tuple[dict[str, object], ...]

    def to_json(self, *, max_chars: int = 12_000) -> str:
        """Serialize a bounded trusted snapshot for one fresh work unit."""

        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)
        if len(payload) <= max_chars:
            return payload
        bounded_symbols: list[dict[str, object]] = []
        bounded: dict[str, object] = {
            "files_scanned": self.files_scanned,
            "truncated": True,
            "symbols": bounded_symbols,
        }
        for symbol in self.symbols:
            bounded_symbols.append(symbol)
            candidate = json.dumps(bounded, ensure_ascii=False, sort_keys=True)
            if len(candidate) > max_chars:
                bounded_symbols.pop()
                break
        return json.dumps(bounded, ensure_ascii=False, sort_keys=True)


def build_schema_contract(
    workspace: Path,
    *,
    max_files: int = 200,
    max_file_bytes: int = 256 * 1024,
    max_symbols: int = 500,
) -> SchemaContract:
    """Extract Python contract shapes from likely schema files, never file bodies."""

    root = workspace.resolve()
    candidates: list[Path] = []
    for directory, names, files in os.walk(root):
        names[:] = sorted(name for name in names if name not in _EXCLUDED_DIRS)
        for filename in sorted(files):
            path = Path(directory) / filename
            if path.suffix == ".py" and (
                path.stem.casefold() in _CONTRACT_STEMS
                or "schema" in {part.casefold() for part in path.parts}
            ):
                candidates.append(path)
                if len(candidates) >= max_files:
                    break
        if len(candidates) >= max_files:
            break

    symbols: list[dict[str, object]] = []
    scanned = 0
    truncated = len(candidates) >= max_files
    for path in candidates:
        try:
            if path.stat().st_size > max_file_bytes:
                truncated = True
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            continue
        scanned += 1
        relative = path.relative_to(root).as_posix()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                fields = []
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(
                        item.target, ast.Name
                    ):
                        fields.append(
                            {
                                "name": item.target.id,
                                "type": ast.unparse(item.annotation)[:300],
                                "required": item.value is None,
                            }
                        )
                symbols.append(
                    {
                        "file": relative,
                        "kind": "class",
                        "name": node.name,
                        "fields": fields[:100],
                    }
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = [argument.arg for argument in node.args.args]
                symbols.append(
                    {
                        "file": relative,
                        "kind": "function",
                        "name": node.name,
                        "arguments": arguments[:50],
                        "returns": (
                            ast.unparse(node.returns)[:300]
                            if node.returns is not None
                            else None
                        ),
                    }
                )
            if len(symbols) >= max_symbols:
                truncated = True
                break
        if len(symbols) >= max_symbols:
            break
    return SchemaContract(scanned, truncated, tuple(symbols))
