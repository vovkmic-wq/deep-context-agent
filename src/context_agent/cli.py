"""Command-line interface for the persistent Deep Context Agent."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO, cast

from dotenv import load_dotenv

from context_agent.autopilot import AutopilotProgress, AutopilotStore
from context_agent.config import SUPPORTED_PROVIDERS, AppConfig, ProviderConfig
from context_agent.context_store import ContextStore
from context_agent.diagnostics import DiagnosticStore, configured_secret_values
from context_agent.errors import AgentError
from context_agent.project_audit import AuditProgress, ProjectAuditStore
from context_agent.providers import create_chat_model
from context_agent.runtime import AgentRuntime, message_text

MAX_PROMPT_FILE_BYTES = 2 * 1024 * 1024


def configure_standard_streams(
    streams: Sequence[TextIO] | None = None,
) -> None:
    """Use deterministic UTF-8 output for Windows pipes and redirected logs."""

    selected_streams = (
        tuple(streams) if streams is not None else (sys.stdout, sys.stderr)
    )
    for stream in selected_streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


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

    audit_parser = subparsers.add_parser(
        "audit",
        help="Run or resume a manifest-backed batched project audit.",
    )
    audit_parser.add_argument(
        "query",
        nargs="?",
        help="Audit objective, or '-' to read it from stdin.",
    )
    audit_parser.add_argument(
        "--allow-write",
        action="store_true",
        help=(
            "Explicitly permit audited changes to existing allocated files. "
            "Without this flag every audit is read-only."
        ),
    )

    job_parser = subparsers.add_parser(
        "job",
        help="Run one objective to completion with persistent adaptive work units.",
    )
    job_parser.add_argument(
        "query",
        nargs="?",
        help="Job objective, or '-' to read it from stdin.",
    )
    job_parser.add_argument(
        "--file",
        type=Path,
        help="Read one UTF-8 job objective from a file (max 2 MiB).",
    )
    job_parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Explicitly permit bounded changes inside AGENT_WORKSPACE.",
    )
    job_parser.add_argument(
        "--report-file",
        type=Path,
        help="Write the final UTF-8 evidence report to this path.",
    )
    job_parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="Optional include glob; repeat the option for multiple patterns.",
    )
    job_parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Optional exclude glob; repeat the option for multiple patterns.",
    )

    job_status_parser = subparsers.add_parser(
        "job-status",
        help="Read persistent autopilot status without invoking an LLM.",
    )
    job_status_parser.add_argument("--job-id", required=True)
    job_status_parser.add_argument("--json", action="store_true")
    audit_parser.add_argument(
        "--report-file",
        type=Path,
        help="Write the complete UTF-8 report directly to this path.",
    )
    audit_parser.add_argument(
        "--report-format",
        choices=("text", "json", "both"),
        default="text",
        help="Detailed report format (default: text).",
    )

    status_parser = subparsers.add_parser(
        "audit-status",
        help="Read persisted audit progress without invoking an LLM.",
    )
    status_parser.add_argument("--run-id", required=True)
    status_parser.add_argument("--json", action="store_true")
    audit_parser.add_argument(
        "--file",
        type=Path,
        help="Read one UTF-8 audit objective from a file (max 2 MiB).",
    )
    audit_parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help=(
            "Maximum batches in this process (1-100); defaults to "
            "AGENT_AUDIT_MAX_BATCHES_PER_REQUEST."
        ),
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

    web_parser = subparsers.add_parser(
        "web",
        help="Start the secure local web interface.",
    )
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)
    web_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Permit a non-loopback bind; requires AGENT_WEB_AUTH_TOKEN.",
    )
    web_parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the UI in the default browser after startup.",
    )

    diagnostics_parser = subparsers.add_parser(
        "diagnostics",
        help="Inspect or purge the durable failed-request journal without an LLM.",
    )
    diagnostic_commands = diagnostics_parser.add_subparsers(
        dest="diagnostics_command",
        required=True,
    )
    diagnostic_list = diagnostic_commands.add_parser("list")
    diagnostic_list.add_argument("--limit", type=int, default=50)
    diagnostic_list.add_argument("--offset", type=int, default=0)
    diagnostic_list.add_argument("--status")
    diagnostic_list.add_argument("--json", action="store_true")
    diagnostic_show = diagnostic_commands.add_parser("show")
    diagnostic_show.add_argument("request_id")
    diagnostic_show.add_argument("--include-query", action="store_true")
    diagnostic_show.add_argument("--json", action="store_true")
    diagnostic_export = diagnostic_commands.add_parser("export")
    diagnostic_export.add_argument("request_id")
    diagnostic_export.add_argument("--output", type=Path, required=True)
    diagnostic_export.add_argument("--include-query", action="store_true")
    diagnostic_purge = diagnostic_commands.add_parser("purge")
    diagnostic_purge.add_argument("--request-id")
    diagnostic_purge.add_argument("--older-than-days", type=int)
    diagnostic_purge.add_argument("--confirm", choices=("PURGE",), required=True)
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
    print(f"project_audit_database={app_config.project_audit_database}")
    print(f"autopilot_database={app_config.autopilot_database}")
    print(f"diagnostics_database={app_config.diagnostics_database}")
    print(f"failure_log_mode={app_config.failure_log_mode}")
    if app_config.failure_log_mode == "full":
        print(
            "warning=failure journal stores full prompts and may contain personal data",
            file=sys.stderr,
        )
    print(f"recursion_limit={app_config.recursion_limit}")
    print(f"audit_batch_size={app_config.audit_batch_size}")
    print(f"audit_max_reads_per_file={app_config.audit_max_reads_per_file}")
    print(f"autopilot_max_work_units={app_config.autopilot_max_work_units}")
    print(f"autopilot_max_replans={app_config.autopilot_max_replans}")
    print(f"autopilot_lease_seconds={app_config.autopilot_lease_seconds}")
    print(f"autopilot_heartbeat_seconds={app_config.autopilot_heartbeat_seconds}")
    print(f"autopilot_unit_timeout_seconds={app_config.autopilot_unit_timeout_seconds}")
    print(f"autopilot_unit_batch_size={app_config.autopilot_unit_batch_size}")
    print(f"autopilot_recursion_limit={app_config.autopilot_recursion_limit}")
    print("audit_mode_default=read-only")
    print(
        "audit_include="
        + (",".join(app_config.audit_include) if app_config.audit_include else "<all>")
    )
    print(
        "audit_exclude="
        + (",".join(app_config.audit_exclude) if app_config.audit_exclude else "<none>")
    )
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("web_dependencies=missing (install .[web])")
    else:
        print("web_dependencies=available")
    static_root = Path(__file__).parent / "static"
    static_ok = all(
        (static_root / name).is_file()
        for name in ("index.html", "app.js", "styles.css")
    )
    print(f"web_static_bundle={'ok' if static_ok else 'missing'}")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 8765))
    except OSError:
        print("web_port_8765=in_use")
    else:
        print("web_port_8765=available")
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


def _run_audit(args: argparse.Namespace, base_dir: Path) -> int:
    def report_progress(
        progress: AuditProgress,
        batch_number: int,
        processed_count: int,
    ) -> None:
        payload = {
            "event": "audit_progress",
            "batch_number": batch_number,
            "processed_count": processed_count,
            **progress.as_dict(),
        }
        print(
            "AUDIT_PROGRESS " + json.dumps(payload, ensure_ascii=False, sort_keys=True),
            flush=True,
        )

    with _runtime(args, base_dir) as runtime:
        print(
            runtime.run_project_audit(
                resolve_ask_query(args),
                thread_id=args.thread,
                max_batches=args.max_batches,
                allow_write=args.allow_write,
                report_file=args.report_file,
                report_format=args.report_format,
                progress_callback=report_progress,
            )
        )
    return 0


def _run_audit_status(args: argparse.Namespace, base_dir: Path) -> int:
    config = _app_config(base_dir)
    with ProjectAuditStore(config.project_audit_database) as store:
        details = store.run_details(args.run_id)
    if args.json:
        print(json.dumps(details, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        progress = details["progress"]
        if not isinstance(progress, dict):
            raise ValueError("Stored audit progress is invalid")
        print(
            f"run_id={details['id']} status={details['status']} "
            f"mode={details['mode']} reviewed={progress['reviewed']}/"
            f"{progress['total']} pending={progress['pending']} "
            f"excluded={progress['excluded']} batches={progress['batches']}"
        )
    return 0


def _run_job(args: argparse.Namespace, base_dir: Path) -> int:
    def report_progress(
        progress: AutopilotProgress,
        audit: AuditProgress | None,
        event: str,
    ) -> None:
        payload: dict[str, object] = {"event": event, **progress.as_dict()}
        if audit is not None:
            payload["audit"] = audit.as_dict()
        print(
            "JOB_PROGRESS " + json.dumps(payload, ensure_ascii=False, sort_keys=True),
            flush=True,
        )

    with _runtime(args, base_dir) as runtime:
        print(
            runtime.run_autopilot_job(
                resolve_ask_query(args),
                thread_id=args.thread,
                allow_write=args.allow_write,
                include_patterns=args.include,
                exclude_patterns=args.exclude,
                report_file=args.report_file,
                progress_callback=report_progress,
            )
        )
    return 0


def _run_job_status(args: argparse.Namespace, base_dir: Path) -> int:
    config = _app_config(base_dir)
    with AutopilotStore(config.autopilot_database) as store:
        details = store.details(args.job_id)
    if args.json:
        print(json.dumps(details, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        progress = details["progress"]
        if not isinstance(progress, dict):
            raise ValueError("Stored autopilot progress is invalid")
        print(
            f"job_id={details['id']} status={details['status']} "
            f"phase={details['phase']} mode={details['mode']} "
            f"units={progress['completed_units']}/{progress['attempts']} "
            f"replans={progress['replans']} "
            f"verification={progress['verification_status']}"
        )
    return 0


def _run_web(args: argparse.Namespace, base_dir: Path) -> int:
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if not 1 <= args.port <= 65_535:
        raise ValueError("port must be between 1 and 65535")
    remote = args.host.casefold() not in loopback_hosts
    if remote and not args.allow_remote:
        raise ValueError("Non-loopback host requires --allow-remote")
    auth_token = os.getenv("AGENT_WEB_AUTH_TOKEN")
    if remote and not auth_token:
        raise ValueError("Remote mode requires AGENT_WEB_AUTH_TOKEN")
    trusted_proxy = os.getenv("AGENT_WEB_TRUSTED_HTTPS_PROXY", "").casefold() in {
        "1",
        "true",
    }
    if remote and not trusted_proxy:
        raise ValueError(
            "Remote mode requires AGENT_WEB_TRUSTED_HTTPS_PROXY=1 and an HTTPS proxy"
        )
    try:
        import uvicorn

        from context_agent.web import create_app
    except ImportError as exc:
        raise AgentError(
            "Web dependencies are missing; install with pip install -e '.[web]'"
        ) from exc

    config = _app_config(base_dir)
    providers = ProviderConfig.priority_from_env(args.provider, args.providers)
    app = create_app(
        config,
        providers,
        allow_remote=remote,
        auth_token=auth_token,
        trusted_https_proxy=trusted_proxy,
    )
    display_host = "127.0.0.1" if args.host == "localhost" else args.host
    url = f"http://{display_host}:{args.port}/"
    print(f"Deep Context Agent Web: {url}", flush=True)
    if remote:
        print(
            "WARNING: remote mode requires an HTTPS reverse proxy in production.",
            file=sys.stderr,
            flush=True,
        )
    if args.open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _diagnostic_store(config: AppConfig) -> DiagnosticStore:
    return DiagnosticStore(
        config.diagnostics_database,
        mode=cast(Any, config.failure_log_mode),
        retention_days=config.failure_log_retention_days,
        max_rows=config.failure_log_max_rows,
        query_max_bytes=config.failure_log_query_max_bytes,
        known_secrets=configured_secret_values(),
    )


def _run_diagnostics(args: argparse.Namespace, base_dir: Path) -> int:
    config = _app_config(base_dir)
    with _diagnostic_store(config) as store:
        if args.diagnostics_command == "list":
            items = store.list_requests(
                limit=args.limit,
                offset=args.offset,
                status=args.status,
            )
            if args.json:
                print(json.dumps(items, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                for item in items:
                    print(
                        f"{item['request_id']} status={item['status']} "
                        f"thread={item['thread_id']} code={item['error_code']} "
                        f"created={item['created_at_utc']}"
                    )
            return 0
        if args.diagnostics_command == "show":
            try:
                item = store.request(
                    args.request_id,
                    include_query=args.include_query,
                )
            except KeyError as exc:
                raise ValueError("Diagnostic request not found") from exc
            if args.json:
                print(json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                for name in (
                    "request_id",
                    "status",
                    "thread_id",
                    "error_code",
                    "created_at_utc",
                    "finished_at_utc",
                    "rollback_success",
                ):
                    print(f"{name}={item.get(name)}")
                if args.include_query:
                    print("query=" + str(item.get("query") or "<not stored>"))
            return 0
        if args.diagnostics_command == "export":
            try:
                item = store.request(
                    args.request_id,
                    include_query=args.include_query,
                )
            except KeyError as exc:
                raise ValueError("Diagnostic request not found") from exc
            payload = {
                "format": "deep-context-agent-diagnostic-v1",
                "item": item,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            print(f"exported={args.output.resolve()}")
            return 0
        if args.diagnostics_command == "purge":
            if args.request_id is None and args.older_than_days is None:
                raise ValueError("Select --request-id or --older-than-days")
            deleted = store.purge(
                request_id=args.request_id,
                older_than_days=args.older_than_days,
            )
            print(f"deleted={deleted}")
            return 0
    raise ValueError("Unknown diagnostics command")


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
    configure_standard_streams()
    base_dir = Path.cwd().resolve()
    _load_environment(base_dir)
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "audit": _run_audit,
        "audit-status": _run_audit_status,
        "ask": _run_ask,
        "chat": _run_chat,
        "doctor": _run_doctor,
        "diagnostics": _run_diagnostics,
        "index": _run_index,
        "job": _run_job,
        "job-status": _run_job_status,
        "search": _run_search,
        "web": _run_web,
    }
    try:
        return commands[args.command](args, base_dir)
    except (AgentError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
