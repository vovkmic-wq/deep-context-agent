"""Command-line interface for the persistent Deep Context Agent."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from dotenv import load_dotenv

from context_agent.config import SUPPORTED_PROVIDERS, AppConfig, ProviderConfig
from context_agent.context_store import ContextStore
from context_agent.errors import AgentError
from context_agent.providers import create_chat_model
from context_agent.runtime import AgentRuntime, message_text

MAX_PROMPT_FILE_BYTES = 2 * 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    """Create the complete CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="context-agent",
        description="Persistent searchable Deep Agent with multiple LLM providers.",
    )
    provider_group = parser.add_mutually_exclusive_group()
    provider_group.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        help=(
            "Single LLM provider; the default chain is glm,openai unless "
            "AGENT_PROVIDER or AGENT_PROVIDER_PRIORITY overrides it."
        ),
    )
    provider_group.add_argument(
        "--providers",
        metavar="PROVIDER1,PROVIDER2",
        help=(
            "Ordered provider failover chain; defaults to "
            "AGENT_PROVIDER_PRIORITY when configured."
        ),
    )
    parser.add_argument(
        "--thread",
        default=os.getenv("AGENT_THREAD_ID", "default"),
        help="Persistent conversation thread ID.",
    )
    parser.add_argument(
        "--no-auto-context",
        action="store_true",
        help=(
            "Disable automatic retrieval for this request/session; tools remain "
            "available."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("chat", help="Start an interactive chat session.")

    ask_parser = subparsers.add_parser("ask", help="Send one request.")
    ask_parser.add_argument(
        "query",
        nargs="?",
        help="User request, or '-' to read one request from stdin.",
    )
    ask_parser.add_argument(
        "--file",
        type=Path,
        help="Read one UTF-8 multi-line request from a file (max 2 MiB).",
    )

    index_parser = subparsers.add_parser(
        "index",
        help="Index a file or directory under AGENT_CONTEXT_ROOT.",
    )
    index_parser.add_argument("path", nargs="?", default=".")

    search_parser = subparsers.add_parser(
        "search",
        help="Search persistent local context without invoking an LLM.",
    )
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=None)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Validate provider configuration without printing secrets.",
    )
    doctor_parser.add_argument(
        "--live",
        action="store_true",
        help="Also make one small model request.",
    )
    return parser


def _load_environment(base_dir: Path) -> None:
    load_dotenv(base_dir / ".env.local", override=False)
    load_dotenv(base_dir / ".env", override=False)


def _app_config(base_dir: Path) -> AppConfig:
    config = AppConfig.from_env(base_dir)
    config.prepare_directories()
    return config


def _run_index(args: argparse.Namespace, base_dir: Path) -> int:
    config = _app_config(base_dir)
    with ContextStore(
        config.context_database,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        max_file_bytes=config.max_file_bytes,
    ) as store:
        report = store.index_path(args.path, config.context_root)
    print(
        f"indexed={report.files_indexed} unchanged={report.files_unchanged} "
        f"skipped={report.files_skipped} chunks={report.chunks_written}"
    )
    for error in report.errors:
        print(f"warning: {error}", file=sys.stderr)
    return 1 if report.errors else 0


def _run_search(args: argparse.Namespace, base_dir: Path) -> int:
    config = _app_config(base_dir)
    limit = args.limit or config.context_top_k
    with ContextStore(config.context_database) as store:
        hits = store.search(args.query, limit=limit)
    if not hits:
        print("No matching context found.")
        return 0
    for index, hit in enumerate(hits, start=1):
        print(f"[{index}] {hit.source} chunk={hit.chunk_index} score={hit.score:.4f}")
        print(hit.content)
    return 0


def _run_doctor(args: argparse.Namespace, base_dir: Path) -> int:
    app_config = _app_config(base_dir)
    providers = ProviderConfig.priority_from_env(args.provider, args.providers)
    provider = providers[0]
    print(f"provider={provider.name}")
    print(f"model={provider.model}")
    print(f"base_url={provider.base_url}")
    print("api_key=configured")
    print("provider_priority=" + ",".join(config.name for config in providers))
    for index, fallback in enumerate(providers[1:], start=1):
        print(f"fallback_{index}_provider={fallback.name}")
        print(f"fallback_{index}_model={fallback.model}")
        print(f"fallback_{index}_base_url={fallback.base_url}")
        print(f"fallback_{index}_api_key=configured")
    print(f"workspace={app_config.workspace}")
    print(f"context_database={app_config.context_database}")
    if args.live:
        failures: list[str] = []
        for candidate in providers:
            try:
                response = create_chat_model(candidate).invoke(
                    "Reply with exactly: OK",
                )
            except Exception as exc:
                error_type = type(exc).__name__
                failures.append(f"{candidate.name} ({error_type})")
                print(
                    f"live_attempt_provider={candidate.name} "
                    f"status=error error_type={error_type}"
                )
                continue
            text = message_text(response).strip()
            print(f"live_provider={candidate.name}")
            print(f"live_response={text}")
            break
        else:
            raise AgentError("All live provider checks failed: " + ", ".join(failures))
    return 0


def _runtime(args: argparse.Namespace, base_dir: Path) -> AgentRuntime:
    providers = ProviderConfig.priority_from_env(args.provider, args.providers)
    return AgentRuntime(
        _app_config(base_dir),
        providers[0],
        fallback_provider_configs=providers[1:],
    )


def read_prompt_file(path: Path) -> str:
    """Read one bounded UTF-8 prompt from an explicitly selected file."""

    try:
        with path.open("rb") as prompt_file:
            payload = prompt_file.read(MAX_PROMPT_FILE_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"Cannot access prompt file '{path}': {exc}") from exc
    if len(payload) > MAX_PROMPT_FILE_BYTES:
        raise ValueError(
            f"Prompt file is too large; maximum is {MAX_PROMPT_FILE_BYTES} bytes"
        )
    try:
        query = payload.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"Prompt file must be valid UTF-8: {path}") from exc
    query = query.replace("\r\n", "\n").replace("\r", "\n")
    if not query.strip():
        raise ValueError("Prompt cannot be empty")
    return query


def resolve_ask_query(args: argparse.Namespace, stdin: TextIO | None = None) -> str:
    """Resolve a single ask request from an argument, file, or stdin."""

    stream = stdin or sys.stdin
    if args.file is not None and args.query is not None:
        raise ValueError("Use either a query argument or --file, not both")
    if args.file is not None:
        return read_prompt_file(args.file)
    if args.query == "-" or (args.query is None and not stream.isatty()):
        query = stream.read(MAX_PROMPT_FILE_BYTES + 1)
        if not query.strip():
            raise ValueError("Prompt from stdin cannot be empty")
        if (
            len(query) > MAX_PROMPT_FILE_BYTES
            or len(query.encode("utf-8")) > MAX_PROMPT_FILE_BYTES
        ):
            raise ValueError("Prompt from stdin exceeds the 2 MiB limit")
        return query
    if args.query is None:
        raise ValueError("Provide a query, use --file, or pipe a prompt to ask -")
    return args.query


def read_chat_query() -> str | None:
    """Read one chat turn, supporting explicit multi-line paste mode."""

    try:
        first_line = input("you> ")
    except (EOFError, KeyboardInterrupt):
        return None
    stripped = first_line.strip()
    command, separator, remainder = stripped.partition(" ")
    if command.casefold() != "/paste":
        return first_line.strip()

    print("Paste mode: enter /end on its own line to send, /cancel to discard.")
    lines = [remainder] if separator and remainder else []
    prompt_bytes = len(remainder.encode("utf-8")) + 1 if lines else 0
    while True:
        try:
            line = input("... ")
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        command = line.strip().casefold()
        if command == "/cancel":
            return ""
        if command == "/end":
            return "\n".join(lines).strip()
        prompt_bytes += len(line.encode("utf-8")) + 1
        if prompt_bytes > MAX_PROMPT_FILE_BYTES:
            raise ValueError("Pasted prompt exceeds the 2 MiB limit")
        lines.append(line)


def _run_ask(args: argparse.Namespace, base_dir: Path) -> int:
    with _runtime(args, base_dir) as runtime:
        print(
            runtime.ask(
                resolve_ask_query(args),
                thread_id=args.thread,
                auto_context=not args.no_auto_context,
            )
        )
    return 0


def _run_chat(args: argparse.Namespace, base_dir: Path) -> int:
    print("Deep Context Agent. Enter /paste for multi-line input, /exit to stop.")
    with _runtime(args, base_dir) as runtime:
        while True:
            query = read_chat_query()
            if query is None:
                print()
                break
            if query.casefold() in {"/exit", "/quit", "exit", "quit"}:
                break
            if not query:
                continue
            try:
                answer = runtime.ask(
                    query,
                    thread_id=args.thread,
                    auto_context=not args.no_auto_context,
                )
            except Exception as exc:
                print(f"request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            print(f"agent> {answer}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and convert expected failures to concise exit messages."""
    base_dir = Path.cwd().resolve()
    _load_environment(base_dir)
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "ask": _run_ask,
        "chat": _run_chat,
        "doctor": _run_doctor,
        "index": _run_index,
        "search": _run_search,
    }
    try:
        return commands[args.command](args, base_dir)
    except (AgentError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
