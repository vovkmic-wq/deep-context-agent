"""Opt-in 0.25 live regression with disposable workspace and existing keys."""

# ruff: noqa: RUF001 -- exact Russian production incident

from __future__ import annotations

import json
import sqlite3
import tempfile
from argparse import ArgumentParser
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from context_agent.autopilot import AutopilotStore
from context_agent.config import AppConfig, ProviderConfig
from context_agent.diagnostics import DiagnosticStore
from context_agent.intent import resolve_intent
from context_agent.semantic_routing import semantic_classifier
from context_agent.task_state import TaskStateStore
from context_agent.web import create_app


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--classifier-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env.local", override=False)
    load_dotenv(root / ".env", override=False)
    providers = tuple(
        replace(p, timeout=45) for p in ProviderConfig.priority_from_env()
    )
    run = Path(tempfile.mkdtemp(prefix="dca-resume-routing-live-"))
    # Use existing model IDs. Tier labels here test routing execution, not a
    # benchmark claiming different performance from the same physical model.
    profiles = [
        {
            "provider": p.name,
            "model": p.model,
            "tier": tier,
            "context_tokens": 128000,
            "tools": True,
        }
        for p in providers
        for tier in ("fast", "standard", "reasoning")
    ]
    cfg = AppConfig(
        project_root=root,
        workspace=run / "workspace",
        context_root=run / "workspace",
        data_dir=run / "data",
        model_profiles=json.dumps(profiles),
        task_timeout_seconds=300,
        task_model_attempts=12,
        model_output_tokens=4096,
        model_call_retries=0,
        semantic_routing_timeout=25,
        autopilot_max_work_units=3,
    )
    print(f"evidence_directory={run}", flush=True)
    if args.classifier_only:
        classifier = semantic_classifier(cfg, providers, thread="live-classifier")
        intent = resolve_intent(
            "Давай вернемся к оставшейся части предыдущей работы.",
            ["isolated-existing-task-id"],
            semantic=classifier,
        )
        assert intent.action == "resume_task" and intent.source == "semantic", intent
        with DiagnosticStore(cfg.diagnostics_database) as journal:
            request_id = journal.list_requests(limit=1)[0]["request_id"]
            generations = journal.request(request_id)["model_generations"]
        report = {
            "result": "PASS",
            "intent": intent.as_dict(),
            "model_attempts": generations,
        }
        (run / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "result": "PASS",
                    "request_id": request_id,
                    "provider": generations[0]["provider"],
                    "model": generations[0]["model"],
                    "reasoning_effort": generations[0]["reasoning_effort"],
                }
            ),
            flush=True,
        )
        return
    observations = []

    def submit(client, query, *, mode="agent", write=True):
        csrf = client.get("/api/runtime").json()["csrf_token"]
        response = client.post(
            "/api/chat",
            headers={"x-csrf-token": csrf},
            json={
                "query": query,
                "thread_id": "live",
                "allow_write": write,
                "mode": mode,
                "auto_context": False,
                "execution_mode": "auto",
                "model_policy": "auto",
            },
        )
        assert response.status_code == 202, response.text
        receipt = response.json()
        stream = client.get(f"/api/events/{receipt['task_id']}").text
        assert "event: failed" not in stream and "event: blocked" not in stream, stream[
            -2000:
        ]
        observations.append(
            {"task_id": receipt["task_id"], "routing": receipt["routing"]}
        )
        print(json.dumps(observations[-1], ensure_ascii=False), flush=True)
        return receipt

    with TestClient(create_app(cfg, providers)) as client:
        first = submit(
            client,
            "Выполни изменение проекта в два этапа. Единственный рабочий файл — "
            "resume.txt в рабочем пространстве. "
            "Этап A: создай его с точным текстом LIVE_A. "
            "Этап B: при отдельной команде продолжения прочитай этот файл и добавь "
            "новую строку LIVE_B, затем перечитай и проверь обе строки. "
            "Сейчас выполни только A. Обязательно вызови save_task_checkpoint: "
            "plan содержит A и B, completed содержит только A, next_step описывает B, "
            "pause=true. Не вызывай другие инструменты кроме write_file и "
            "save_task_checkpoint. Остановись после сохранения checkpoint.",
        )
        task_id = first["active_task"]["task_id"]
        assert (cfg.workspace / "resume.txt").read_text(
            encoding="utf-8"
        ).strip() == "LIVE_A"
        with TaskStateStore(cfg.context_database) as store:
            checkpoint = store.checkpoint(store.resolve("live", cfg.workspace, task_id))
        assert checkpoint.get("next_step") and checkpoint.get("pause")
        jobs = client.get("/api/jobs").json()["items"]
        assert len(jobs) == 1
        job_id = jobs[0]["id"]
        submit(
            client,
            "Проанализируй лог:\nWorkspace access denied\n"
            "Проведи полный аудит проекта\nЭто данные прежнего хода.",
            write=False,
        )
    with TestClient(create_app(cfg, providers)) as client:
        continued = submit(
            client, "продолжи выполнение задачи в соответствии с тз и промтом"
        )
        assert continued["active_task"]["task_id"] == task_id
        content = (cfg.workspace / "resume.txt").read_text(encoding="utf-8")
        assert content.strip().splitlines() == ["LIVE_A", "LIVE_B"], content
        jobs = client.get("/api/jobs").json()["items"]
        assert len(jobs) == 1 and jobs[0]["id"] == job_id
        submit(
            client,
            "Прочитай /workspace/resume.txt и покажи обе строки.",
            mode="ask",
            write=False,
        )
        assert (cfg.workspace / "resume.txt").read_text(encoding="utf-8") == content
    with AutopilotStore(cfg.autopilot_database) as store:
        details = store.details(job_id)
    assert details["audit_run_id"] is None
    assert {u["phase"] for u in details["work_units"]} == {"execute"}
    with sqlite3.connect(cfg.project_audit_database) as db:
        assert db.execute("SELECT count(*) FROM audit_runs").fetchone()[0] == 0
    classifier = semantic_classifier(cfg, providers, thread="live-classifier")
    intent = resolve_intent(
        "Давай вернемся к оставшейся части предыдущей работы.",
        [task_id],
        semantic=classifier,
    )
    assert intent.action == "resume_task" and intent.source == "semantic", intent
    with sqlite3.connect(cfg.diagnostics_database) as db:
        request_ids = [
            r[0] for r in db.execute("SELECT request_id FROM request_attempts")
        ]
    with DiagnosticStore(cfg.diagnostics_database) as journal:
        records = [journal.request(r) for r in request_ids]
    models = [g for r in records for g in r.get("model_generations", [])]
    assert any(g.get("model_route", {}).get("mode") == "adaptive" for g in models)
    report = {
        "result": "PASS",
        "observations": observations,
        "job_id": job_id,
        "task_id": task_id,
        "semantic_intent": intent.as_dict(),
        "model_attempts": models,
        "audit_runs": 0,
    }
    (run / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "result": "PASS",
                "job_id": job_id,
                "model_attempts": len(models),
                "semantic": intent.source,
                "audit_runs": 0,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
