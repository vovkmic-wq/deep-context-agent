"""Opt-in 0.26 live test for runtime-owned soft yield and durable receipts."""

# ruff: noqa: RUF001 -- exact Russian live acceptance instructions

from __future__ import annotations

import json
import tempfile
from argparse import ArgumentParser
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from context_agent.autopilot import AutopilotStore
from context_agent.config import AppConfig, ProviderConfig
from context_agent.diagnostics import DiagnosticStore
from context_agent.web import create_app


def _providers(root: Path) -> tuple[ProviderConfig, ...]:
    load_dotenv(root / ".env.local", override=False)
    load_dotenv(root / ".env", override=False)
    configured = ProviderConfig.priority_from_env()
    selected: list[ProviderConfig] = []
    for provider in configured:
        if provider.name == "zhipu":
            selected.append(replace(provider, model="glm-5.3-flash", timeout=45))
        else:
            selected.append(replace(provider, timeout=45))
    if not selected or selected[0].name != "zhipu":
        raise RuntimeError("The live test requires configured Zhipu as primary")
    return tuple(selected)


def _create_fixture(workspace: Path) -> None:
    workspace.mkdir(parents=True)
    for index in range(9):
        (workspace / f"part-{index:02d}.txt").write_text(
            f"PART_{index:02d}_ANCHOR\n",
            encoding="utf-8",
        )
    (workspace / "pages.txt").write_text(
        "".join(f"PAGE_LINE_{index:03d}\n" for index in range(600)),
        encoding="utf-8",
    )


def _objective() -> str:
    parts = ", ".join(f"/workspace/part-{index:02d}.txt" for index in range(9))
    return (
        "Измени тестовый проект строго по шагам. Не запускай полный аудит, glob, "
        "grep и save_task_checkpoint: этот live-тест проверяет независимый runtime "
        "ledger. Сначала прочитай каждый из 9 точных файлов по одному разу: "
        f"{parts}. Затем прочитай /workspace/pages.txt пятью неперекрывающимися "
        "вызовами read_file с offset 0, 120, 240, 360, 480 и limit 120. После "
        "успешных чтений создай /workspace/LIVE_BOUNDED_RESULT.txt с единственной "
        "строкой LIVE_BOUNDED_PROGRESS_OK. Не повторяй уже подтверждённые операции. "
        "Заверши только после создания файла."
    )


def _submit(client: TestClient, query: str, *, continuation: str | None = None):
    csrf = client.get("/api/runtime").json()["csrf_token"]
    response = client.post(
        "/api/chat",
        headers={"x-csrf-token": csrf},
        json={
            "query": query,
            "thread_id": "bounded-live",
            "allow_write": True,
            "mode": "agent",
            "auto_context": False,
            "execution_mode": "autopilot",
            "model_policy": "manual",
            "provider": "zhipu",
            "model": "glm-5.3-flash",
            "continuation_task_id": continuation,
        },
    )
    assert response.status_code == 202, response.text
    receipt = response.json()
    stream = client.get(f"/api/events/{receipt['task_id']}").text
    assert "event: failed" not in stream, stream[-4_000:]
    assert "event: blocked" not in stream, stream[-4_000:]
    return receipt, stream


def _run_once(root: Path, providers: tuple[ProviderConfig, ...], ordinal: int):
    run = Path(tempfile.mkdtemp(prefix=f"dca-bounded-live-{ordinal}-"))
    workspace = run / "workspace"
    _create_fixture(workspace)
    config = AppConfig(
        project_root=root,
        workspace=workspace,
        context_root=workspace,
        data_dir=run / "data",
        embedding_enabled=False,
        task_timeout_seconds=900,
        task_model_attempts=80,
        model_output_tokens=2_048,
        model_call_retries=0,
        autopilot_max_work_units=16,
        autopilot_soft_model_calls_per_unit=3,
        autopilot_retry_attempts=4,
        autopilot_max_replans=8,
        autopilot_recursion_limit=40,
        autopilot_unit_timeout_seconds=180,
    )
    print(f"evidence_directory_{ordinal}={run}", flush=True)
    with TestClient(create_app(config, providers)) as client:
        first, first_stream = _submit(client, _objective())
        task_id = first["active_task"]["task_id"]
        jobs = client.get("/api/jobs").json()["items"]
        assert len(jobs) == 1
        job_id = jobs[0]["id"]

    result_path = workspace / "LIVE_BOUNDED_RESULT.txt"
    assert result_path.read_text(encoding="utf-8").strip() == (
        "LIVE_BOUNDED_PROGRESS_OK"
    )
    with AutopilotStore(config.autopilot_database) as store:
        before_restart = store.details(job_id)
    progress = before_restart["progress"]
    receipts = before_restart["tool_receipts"]
    successful_reads = [
        item
        for item in receipts
        if item["operation"] == "read_file" and item["status"] == "success"
    ]
    page_offsets = {
        int(item["start_line"])
        for item in successful_reads
        if item["target"] == "/workspace/pages.txt"
        and item.get("start_line") is not None
    }
    assert progress["yielded_units"] >= 1
    assert len(successful_reads) >= 14
    assert len(page_offsets) >= 5
    assert len(before_restart["work_units"]) >= 2
    assert before_restart["audit_run_id"] is None

    with DiagnosticStore(config.diagnostics_database) as journal:
        parent = journal.resolve_request(first["task_id"])
    assert len(parent["children"]) >= 2

    # Restart the whole Web app on the same DB. A continuation may verify the
    # result, but it must not replay the already successful mutation.
    with TestClient(create_app(config, providers)) as client:
        _, continued_stream = _submit(
            client,
            "Продолжи сохранённую задачу: проверь результат и не повторяй "
            "подтверждённые операции.",
            continuation=task_id,
        )
    with AutopilotStore(config.autopilot_database) as store:
        after_restart = store.details(job_id)
    result_writes = [
        item
        for item in after_restart["tool_receipts"]
        if item["target"] == "/workspace/LIVE_BOUNDED_RESULT.txt"
        and item["operation"] in {"write_file", "edit_file"}
        and item["status"] == "success"
    ]
    assert len(result_writes) == 1
    assert result_path.read_text(encoding="utf-8").strip() == (
        "LIVE_BOUNDED_PROGRESS_OK"
    )
    report = {
        "result": "PASS",
        "provider": "zhipu",
        "model": "glm-5.3-flash",
        "job_id": job_id,
        "task_id": task_id,
        "yielded_units": progress["yielded_units"],
        "work_units_before_restart": len(before_restart["work_units"]),
        "work_units_after_restart": len(after_restart["work_units"]),
        "successful_reads": len(successful_reads),
        "page_ranges": len(page_offsets),
        "child_requests": len(parent["children"]),
        "successful_result_writes": len(result_writes),
        "first_terminal_partial": "event: partial" in first_stream,
        "continued_terminal_partial": "event: partial" in continued_stream,
    }
    (run / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 3))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    providers = _providers(root)
    reports = [_run_once(root, providers, index + 1) for index in range(args.repeat)]
    print(
        json.dumps(
            {"result": "PASS", "runs": len(reports)},
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
