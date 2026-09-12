"""A green subset or mixed context must never become a production PASS."""

from dataclasses import replace

from context_agent.project_checks import ProjectCheckResult, verification_passed


def receipt(check="pytest", context="same", checks=("pytest",)):
    return ProjectCheckResult(
        check,
        (),
        0,
        0.1,
        "passed",
        "",
        context={
            "schema_version": 2,
            "run_id": "fixture-run",
            "project_root": "/workspace",
            "checks": list(checks),
            "context_id": context,
            "source_sha256": "source",
            "environment_sha256": "env",
        },
    )


def test_complete_same_context_passes():
    assert verification_passed([receipt()], ("pytest",))


def test_missing_check_cannot_pass():
    assert not verification_passed([receipt()], ("pytest", "compileall"))


def test_mixed_context_cannot_pass():
    assert not verification_passed(
        [receipt(), receipt("compileall", "other")], ("pytest", "compileall")
    )


def test_duplicate_or_legacy_receipts_cannot_pass():
    assert not verification_passed([receipt(), receipt()], ("pytest",))
    assert not verification_passed([replace(receipt(), context={})], ("pytest",))
    legacy = {k: v for k, v in receipt().context.items() if k != "schema_version"}
    assert not verification_passed([replace(receipt(), context=legacy)], ("pytest",))


def test_status_and_exit_code_must_both_pass():
    assert not verification_passed([replace(receipt(), status="stale")], ("pytest",))
    assert not verification_passed([replace(receipt(), return_code=1)], ("pytest",))


def test_incomplete_runtime_evidence_does_not_start_repair(tmp_path, monkeypatch):
    from conftest import SequenceChatModel, complete_check_results
    from langchain_core.messages import AIMessage

    from context_agent.config import AppConfig, ProviderConfig
    from context_agent.runtime import AgentRuntime

    config = AppConfig(
        project_root=tmp_path,
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        context_root=tmp_path / "workspace",
    )
    config.prepare_directories()
    (config.workspace / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    model = SequenceChatModel(responses=[AIMessage(content="must never be invoked")])
    provider = ProviderConfig(
        name="lmstudio",
        model="fixture",
        base_url="http://localhost:1234/v1",
        api_key="fixture",
    )
    with AgentRuntime(config, provider, model=model) as runtime:
        monkeypatch.setattr(
            runtime.project_check_runner,
            "run",
            lambda *, project_root: complete_check_results(project_root)[:1],
        )
        answer = runtime.run_autopilot_job(
            "Verify the project", workflow="verification-only", allow_write=True
        )
    assert "verification_evidence_incomplete" in answer
    assert model.generation_attempts == 0
