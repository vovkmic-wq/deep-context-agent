"""Contract tests for the canonical machine-readable acceptance prompt."""

from pathlib import Path

from context_agent.runtime import (
    ToolAuditEntry,
    evaluate_acceptance_manifest,
    parse_acceptance_manifest,
)


def test_canonical_acceptance_manifest_accepts_the_intended_ordered_audit() -> None:
    project_root = Path(__file__).resolve().parents[1]
    query = (project_root / "acceptance-prompt.txt").read_text(encoding="utf-8")
    manifest = parse_acceptance_manifest(query)
    assert manifest is not None
    audit = (
        ToolAuditEntry("write_todos", None, "success", "planned"),
        ToolAuditEntry("runtime_info", None, "success", "metadata"),
        ToolAuditEntry(
            "make_directory", "/workspace/acceptance-v040-82641", "success", "ok"
        ),
        ToolAuditEntry(
            "write_file",
            "/workspace/acceptance-v040-82641/result.txt",
            "success",
            "ok",
        ),
        ToolAuditEntry(
            "read_file",
            "/workspace/acceptance-v040-82641/result.txt",
            "success",
            "ok",
        ),
        ToolAuditEntry(
            "edit_file",
            "/workspace/acceptance-v040-82641/result.txt",
            "success",
            "ok",
        ),
        ToolAuditEntry(
            "read_file",
            "/workspace/acceptance-v040-82641/result.txt",
            "success",
            "ok",
        ),
        ToolAuditEntry(
            "write_file",
            "/workspace/acceptance-v040-82641/decoy.txt",
            "success",
            "ok",
        ),
        ToolAuditEntry(
            "read_file",
            "/workspace/acceptance-v040-82641/result.txt",
            "success",
            "ok",
        ),
        ToolAuditEntry(
            "read_file",
            "/workspace/acceptance-v040-82641/missing.txt",
            "error",
            "missing",
        ),
        ToolAuditEntry(
            "read_file",
            "/workspace/outside-agent-82641.txt",
            "error",
            "missing",
        ),
        ToolAuditEntry(
            "write_file",
            "/workspace/root-sentinel-82641.txt",
            "success",
            "ok",
        ),
        ToolAuditEntry("remove_path", "/workspace", "denied", "blocked"),
        ToolAuditEntry(
            "read_file",
            "/workspace/root-sentinel-82641.txt",
            "success",
            "ok",
        ),
        ToolAuditEntry(
            "fetch_web_page",
            "http://127.0.0.1:8000/private",
            "error",
            "blocked",
        ),
        ToolAuditEntry("get_pypi_package_info", "langchain", "success", "verified"),
        ToolAuditEntry("list_context_sources", None, "success", "listed"),
        ToolAuditEntry(
            "search_context",
            "PERSISTENT_PHRASE_ЯНТАРНЫЙ_МАЯК_62941",
            "success",
            "searched",
        ),
        ToolAuditEntry(
            "remove_path",
            "/workspace/acceptance-v040-82641 [recursive=true]",
            "success",
            "removed",
        ),
        ToolAuditEntry(
            "read_file",
            "/workspace/acceptance-v040-82641/result.txt",
            "error",
            "missing",
        ),
        ToolAuditEntry(
            "remove_path",
            "/workspace/root-sentinel-82641.txt",
            "success",
            "removed",
        ),
        ToolAuditEntry(
            "read_file",
            "/workspace/root-sentinel-82641.txt",
            "error",
            "missing",
        ),
    )

    evaluation = evaluate_acceptance_manifest(manifest, audit)

    assert evaluation.failed == 0
    assert evaluation.pending == 1
    assert evaluation.tool_counts["read_file"] == 8
    assert evaluation.tool_counts["remove_path"] == 3
