"""Contract tests for the canonical machine-readable acceptance prompt."""

from pathlib import Path

from context_agent.runtime import (
    ToolAuditEntry,
    evaluate_acceptance_manifest,
    explicit_tool_call_budget,
    parse_acceptance_manifest,
)

INITIAL_SHA256 = "70144fc8bcb56b32c3a4f057f0654170a284810df5f2b8159a7d24f40a731348"
EDITED_SHA256 = "498d34f0278856004f313f3a73d41fa6837d4c4b91cfa337e25bacf58d218410"
SENTINEL_SHA256 = "22d8cad50fb6e673c9c4c36e05573da3d55b6968d0e2681542d50c71accb2df1"


def test_canonical_acceptance_manifest_accepts_the_intended_ordered_audit() -> None:
    project_root = Path(__file__).resolve().parents[1]
    query = (project_root / "acceptance-prompt.txt").read_text(encoding="utf-8")
    manifest = parse_acceptance_manifest(query)
    assert manifest is not None
    audit = (
        ToolAuditEntry("write_todos", None, "error", "invalid plan"),
        ToolAuditEntry("write_todos", None, "success", "planned"),
        ToolAuditEntry("write_todos", None, "success", "updated"),
        ToolAuditEntry("runtime_info", None, "success", "metadata"),
        ToolAuditEntry(
            "make_directory", "/workspace/acceptance-v040-82641", "success", "ok"
        ),
        ToolAuditEntry(
            "write_file",
            "/workspace/acceptance-v040-82641/result.txt",
            "success",
            "ok",
            content_sha256=INITIAL_SHA256,
        ),
        ToolAuditEntry(
            "read_file",
            "/workspace/acceptance-v040-82641/result.txt",
            "success",
            "ok",
            content_sha256=INITIAL_SHA256,
        ),
        ToolAuditEntry(
            "edit_file",
            "/workspace/acceptance-v040-82641/result.txt",
            "success",
            "ok",
            content_sha256=EDITED_SHA256,
        ),
        ToolAuditEntry(
            "read_file",
            "/workspace/acceptance-v040-82641/result.txt",
            "success",
            "ok",
            content_sha256=EDITED_SHA256,
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
            content_sha256=EDITED_SHA256,
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
            content_sha256=SENTINEL_SHA256,
        ),
        ToolAuditEntry("remove_path", "/workspace", "denied", "blocked"),
        ToolAuditEntry(
            "read_file",
            "/workspace/root-sentinel-82641.txt",
            "success",
            "ok",
            content_sha256=SENTINEL_SHA256,
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
    assert evaluation.blocked == 0
    assert evaluation.pending == 1
    assert evaluation.tool_counts["read_file"] == 8
    assert evaluation.tool_counts["remove_path"] == 3


def test_restart_acceptance_requires_one_non_empty_search() -> None:
    project_root = Path(__file__).resolve().parents[1]
    query = (project_root / "restart-acceptance-prompt.txt").read_text(encoding="utf-8")
    manifest = parse_acceptance_manifest(query)
    assert manifest is not None

    empty = evaluate_acceptance_manifest(
        manifest,
        (
            ToolAuditEntry(
                "search_context",
                "PERSISTENT_PHRASE_ЯНТАРНЫЙ_МАЯК_62941",
                "success",
                "Returned 0 result(s).",
                result_count=0,
            ),
        ),
    )
    found = evaluate_acceptance_manifest(
        manifest,
        (
            ToolAuditEntry(
                "search_context",
                "PERSISTENT_PHRASE_ЯНТАРНЫЙ_МАЯК_62941",
                "success",
                "Returned 4 result(s).",
                result_count=4,
            ),
        ),
    )

    assert empty.failed == 1
    assert found.failed == 0
    assert found.passed == 2


def test_ozon_strict_prompt_enforces_read_only_tool_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    query = (project_root / "ozon-strict-compliance-prompt.txt").read_text(
        encoding="utf-8"
    )
    manifest = parse_acceptance_manifest(query)
    budget = explicit_tool_call_budget(query)

    assert manifest is not None
    assert manifest.exact_tool_call_counts == {
        "list_context_sources": 1,
        "ls": 1,
        "search_context": 2,
        "read_context_window": 2,
        "read_file": 14,
    }
    assert len(manifest.required_events) == 20
    assert budget.total == 20
    assert budget.per_tool["read_file"] == 14
    for tool_name in {
        "web_search",
        "fetch_web_page",
        "get_pypi_package_info",
        "write_todos",
        "write_file",
        "edit_file",
        "make_directory",
        "remove_path",
        "glob",
        "grep",
    }:
        assert budget.per_tool[tool_name] == 0
