"""Approved repair incidents derived from authoritative failed verification."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from context_agent.diagnostics import redact_sensitive_text
from context_agent.paths import PathSecurityError, resolve_inside
from context_agent.project_checks import resolve_project_root

_PATH = re.compile(
    r"(?im)(?P<path>(?:src|tests|test|app|web|scripts|config)[\\/]"
    r"[^\s:|]+\.(?:py|toml|json|ya?ml|js|ts|tsx|css|html))(?:[:](?P<line>\d+))?"
)
_ERROR_CODE = re.compile(r"(?m)^([A-Z]{1,5}\d{2,4})\b")
_MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class RepairConflictError(ValueError):
    """A repair proposal or approval failed a trusted precondition."""


@dataclass(frozen=True, slots=True)
class RepairProposal:
    id: str
    source_job_id: str
    source_task_id: str | None
    thread_id: str
    workspace: str
    source_revision: int
    project_root: str
    root_cause_key: str
    evidence: tuple[dict[str, Any], ...]
    evidence_sha256: str
    plan: dict[str, Any]
    plan_sha256: str
    allowed_paths: tuple[str, ...]
    status: str

    def public(self) -> dict[str, object]:
        """Return a bounded, host-path-free proposal DTO."""

        return {
            "proposal_id": self.id,
            "source_job_id": self.source_job_id,
            "source_task_id": self.source_task_id,
            "source_revision": self.source_revision,
            "project_root": self.project_root,
            "root_cause_key": self.root_cause_key,
            "evidence": list(self.evidence),
            "evidence_sha256": self.evidence_sha256,
            "plan": self.plan,
            "plan_sha256": self.plan_sha256,
            "allowed_paths": list(self.allowed_paths),
            "status": self.status,
        }


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _virtual_path(workspace: Path, path: Path) -> str:
    relative = path.resolve().relative_to(workspace.resolve()).as_posix()
    return "/workspace" if not relative else f"/workspace/{relative}"


class VerificationRepairStore:
    """Persist immutable proposals, approvals, relations and append-only events."""

    def __init__(
        self,
        database: Path,
        *,
        workspace: Path,
        known_secrets: tuple[str, ...] = (),
    ) -> None:
        self.workspace = workspace.resolve()
        self.database = database
        self.known_secrets = known_secrets
        self.db = sqlite3.connect(database, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA foreign_keys=ON")
        self._schema()

    def __enter__(self) -> VerificationRepairStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.db.close()

    def _schema(self) -> None:
        with self.db:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS verification_repair_proposals (
                    id TEXT PRIMARY KEY,
                    source_job_id TEXT NOT NULL REFERENCES autopilot_jobs(id),
                    source_task_id TEXT,
                    thread_id TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    source_revision INTEGER NOT NULL,
                    project_root TEXT NOT NULL,
                    root_cause_key TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    plan_sha256 TEXT NOT NULL,
                    allowed_paths_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'awaiting_approval',
                    created_at REAL NOT NULL,
                    UNIQUE(source_job_id, source_revision, root_cause_key)
                );
                CREATE TABLE IF NOT EXISTS verification_repair_relations (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL UNIQUE
                        REFERENCES verification_repair_proposals(id),
                    source_job_id TEXT NOT NULL,
                    source_task_id TEXT,
                    repair_job_id TEXT NOT NULL UNIQUE,
                    repair_task_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    plan_sha256 TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    source_revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verification_repair_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    relation_id TEXT,
                    source_job_id TEXT NOT NULL,
                    repair_job_id TEXT,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_repair_source
                    ON verification_repair_proposals(source_job_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_repair_events
                    ON verification_repair_events(source_job_id, sequence);
                """
            )

    def proposals(self, source_job_id: str) -> list[RepairProposal]:
        """Return or deterministically materialize proposals for one failed job."""

        rows = self.db.execute(
            "SELECT * FROM verification_repair_proposals WHERE source_job_id=? "
            "ORDER BY created_at,id",
            (source_job_id,),
        ).fetchall()
        if rows:
            return [self._decode_proposal(row) for row in rows]
        source = self._source_job(source_job_id)
        self._validate_source(source)
        results = json.loads(str(source["verification_results"] or "[]"))
        blocker = json.loads(str(source["blocker_json"] or "{}"))
        project_root = self._project_root(blocker)
        created: list[RepairProposal] = []
        for result in results:
            if not isinstance(result, Mapping) or result.get("status") == "passed":
                continue
            proposal = self._proposal(source, result, project_root)
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO verification_repair_proposals VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        proposal.id,
                        proposal.source_job_id,
                        proposal.source_task_id,
                        proposal.thread_id,
                        proposal.workspace,
                        proposal.source_revision,
                        proposal.project_root,
                        proposal.root_cause_key,
                        _canonical(proposal.evidence),
                        proposal.evidence_sha256,
                        _canonical(proposal.plan),
                        proposal.plan_sha256,
                        _canonical(proposal.allowed_paths),
                        proposal.status,
                        time.time(),
                    ),
                )
                self._event(
                    source_job_id,
                    "repair_proposed",
                    {
                        "proposal_id": proposal.id,
                        "root_cause_key": proposal.root_cause_key,
                    },
                )
            created.append(proposal)
        if not created:
            raise RepairConflictError("Failed verification has no repairable evidence")
        return created

    def get_proposal(self, proposal_id: str) -> RepairProposal:
        row = self.db.execute(
            "SELECT * FROM verification_repair_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise RepairConflictError("Repair proposal not found")
        return self._decode_proposal(row)

    def relation_by_idempotency(self, key: str) -> dict[str, object] | None:
        row = self.db.execute(
            "SELECT * FROM verification_repair_relations WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        return dict(row) if row is not None else None

    def relation_by_proposal(self, proposal_id: str) -> dict[str, object] | None:
        row = self.db.execute(
            "SELECT * FROM verification_repair_relations WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def reserve(
        self,
        proposal: RepairProposal,
        *,
        idempotency_key: str,
        repair_task_id: str,
        repair_job_id: str,
        confirmed_by: str,
    ) -> dict[str, object]:
        """Seal one explicit approval before task/job materialization."""

        existing = self.relation_by_idempotency(idempotency_key)
        if existing is not None:
            if str(existing["proposal_id"]) != proposal.id:
                raise RepairConflictError("Idempotency key belongs to another proposal")
            return existing
        existing = self.relation_by_proposal(proposal.id)
        if existing is not None:
            return existing
        source = self._source_job(proposal.source_job_id)
        if int(source["checkpoint_revision"]) != proposal.source_revision:
            raise RepairConflictError("Source checkpoint changed; evidence is stale")
        relation_id = uuid4().hex
        now = time.time()
        with self.db:
            self.db.execute(
                "INSERT INTO verification_repair_relations VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    relation_id,
                    proposal.id,
                    proposal.source_job_id,
                    proposal.source_task_id,
                    repair_job_id,
                    repair_task_id,
                    idempotency_key,
                    proposal.plan_sha256,
                    proposal.evidence_sha256,
                    proposal.source_revision,
                    "approved",
                    confirmed_by,
                    now,
                    now,
                ),
            )
            self.db.execute(
                "UPDATE verification_repair_proposals SET status='approved' WHERE id=?",
                (proposal.id,),
            )
            self._event(
                proposal.source_job_id,
                "approval_confirmed",
                {"proposal_id": proposal.id, "plan_sha256": proposal.plan_sha256},
                relation_id=relation_id,
                repair_job_id=repair_job_id,
            )
        return self.relation_by_idempotency(idempotency_key) or {}

    def mark_created(self, repair_job_id: str) -> None:
        """Move an approved relation to repairing after task/job creation."""

        with self.db:
            row = self.db.execute(
                "SELECT * FROM verification_repair_relations WHERE repair_job_id=?",
                (repair_job_id,),
            ).fetchone()
            if row is None:
                raise RepairConflictError("Repair relation not found")
            self.db.execute(
                "UPDATE verification_repair_relations SET "
                "status='repairing',updated_at=? "
                "WHERE repair_job_id=?",
                (time.time(), repair_job_id),
            )
            self._event(
                str(row["source_job_id"]),
                "repair_task_created",
                {"repair_task_id": str(row["repair_task_id"])},
                relation_id=str(row["id"]),
                repair_job_id=repair_job_id,
            )

    def relation_for_repair(self, repair_job_id: str) -> dict[str, object] | None:
        row = self.db.execute(
            "SELECT r.*,p.project_root,p.allowed_paths_json,"
            "p.plan_json,p.evidence_json "
            "FROM verification_repair_relations r JOIN verification_repair_proposals p "
            "ON p.id=r.proposal_id WHERE r.repair_job_id=?",
            (repair_job_id,),
        ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["allowed_paths"] = json.loads(str(value.pop("allowed_paths_json")))
        value["plan"] = json.loads(str(value.pop("plan_json")))
        if _sha(value["plan"]) != value["plan_sha256"]:
            raise RepairConflictError("STALE_EVIDENCE: repair plan integrity failure")
        evidence = json.loads(str(value.pop("evidence_json")))
        if _sha(evidence) != value["evidence_sha256"]:
            raise RepairConflictError(
                "STALE_EVIDENCE: repair evidence integrity failure"
            )
        value["verification_evidence"] = evidence
        value["verification_context_ids"] = sorted(
            {
                str(item["persisted_context_id"])
                for item in evidence
                if isinstance(item, dict) and item.get("persisted_context_id")
            }
        )
        return value

    def approved_operation(self, job_id: str) -> dict[str, object] | None:
        relation = self.relation_for_repair(job_id)
        if relation is None:
            return None
        proposal = self.get_proposal(str(relation["proposal_id"]))
        if not proposal.allowed_paths:
            raise RepairConflictError("Approved repair has no mutation target")
        return {
            "phase": "repair",
            "operation": "edit_file",
            "target": proposal.allowed_paths[0],
            "objective": (
                "Repair the explicitly approved failure, without weakening checks."
            ),
            "required_evidence_ids": [str(e["evidence_id"]) for e in proposal.evidence],
            "expected_effect": proposal.plan["expected_effect"],
            "verification_commands": [str(e["check"]) for e in proposal.evidence],
            "component": Path(proposal.allowed_paths[0]).name,
        }

    def record_outcome(
        self,
        repair_job_id: str,
        *,
        job_status: str,
        verification_status: str,
        error_code: str | None,
    ) -> None:
        """Mirror a terminal worker result into the immutable incident trail."""

        relation = self.relation_for_repair(repair_job_id)
        if relation is None:
            return
        relation_status = {
            ("complete", "passed"): "verified",
            ("cancelled", verification_status): "cancelled",
            ("blocked", verification_status): "blocked",
        }.get((job_status, verification_status), "repairing")
        if relation_status == "repairing":
            return
        payload = {
            "job_status": job_status,
            "verification_status": verification_status,
            "error_code": error_code or "",
        }
        with self.db:
            self.db.execute(
                "UPDATE verification_repair_relations SET status=?,updated_at=? "
                "WHERE repair_job_id=?",
                (relation_status, time.time(), repair_job_id),
            )
            self._event(
                str(relation["source_job_id"]),
                "repair_terminal",
                payload,
                relation_id=str(relation["id"]),
                repair_job_id=repair_job_id,
            )

    def assert_write(self, repair_job_id: str, target: str) -> None:
        """Enforce the approval seal and exact file territory before a mutation."""

        relation = self.relation_for_repair(repair_job_id)
        if relation is None:
            return
        if relation["status"] not in {"approved", "repairing"}:
            raise RepairConflictError("Repair write gate is closed")
        proposal = self.get_proposal(str(relation["proposal_id"]))
        source = self._source_job(proposal.source_job_id)
        if int(source["checkpoint_revision"]) != proposal.source_revision:
            self._invalidate(relation, "source_revision_changed")
            raise RepairConflictError("STALE_EVIDENCE: source checkpoint changed")
        if (
            _sha(proposal.plan) != proposal.plan_sha256
            or _sha(proposal.evidence) != proposal.evidence_sha256
        ):
            self._invalidate(relation, "approval_hash_mismatch")
            raise RepairConflictError("STALE_EVIDENCE: approval seal mismatch")
        try:
            resolved = resolve_inside(self.workspace, target).resolve()
            allowed = {
                resolve_inside(self.workspace, path).resolve()
                for path in proposal.allowed_paths
            }
        except PathSecurityError as exc:
            raise RepairConflictError("Repair target is outside workspace") from exc
        if resolved not in allowed:
            raise RepairConflictError(
                "Repair target is outside the approved file territory"
            )
        receipts = int(
            self.db.execute(
                "SELECT COUNT(*) FROM autopilot_tool_receipts "
                "WHERE job_id=? AND verified_progress=1 AND operation IN "
                "('write_file','edit_file','make_directory','remove_path')",
                (repair_job_id,),
            ).fetchone()[0]
        )
        if receipts == 0:
            self._assert_approved_baseline(relation, proposal)

    def assert_verification_state(self, repair_job_id: str) -> None:
        """Reject independent verification if mutation receipts have drifted."""

        relation = self.relation_for_repair(repair_job_id)
        if relation is None:
            return
        rows = self.db.execute(
            "SELECT target,after_sha256 FROM autopilot_tool_receipts "
            "WHERE job_id=? AND verified_progress=1 AND operation IN "
            "('write_file','edit_file','make_directory','remove_path') "
            "AND LENGTH(after_sha256)=64 "
            "ORDER BY created_at",
            (repair_job_id,),
        ).fetchall()
        if not rows:
            raise RepairConflictError("Repair has no verified mutation receipt")
        latest = {str(row["target"]): str(row["after_sha256"]) for row in rows}
        for target, expected in latest.items():
            try:
                resolved = resolve_inside(self.workspace, target).resolve()
            except PathSecurityError as exc:
                raise RepairConflictError(
                    "Repair receipt target is outside workspace"
                ) from exc
            actual = _file_sha256(resolved) if resolved.is_file() else "missing"
            if actual != expected:
                self._invalidate(relation, "mutation_receipt_drift")
                raise RepairConflictError(
                    "STALE_EVIDENCE: repaired file changed after mutation"
                )

    def _assert_approved_baseline(
        self,
        relation: Mapping[str, object],
        proposal: RepairProposal,
    ) -> None:
        baseline = proposal.plan.get("baseline_sha256", {})
        if not isinstance(baseline, Mapping):
            self._invalidate(relation, "missing_baseline")
            raise RepairConflictError("STALE_EVIDENCE: baseline is unavailable")
        for target, expected in baseline.items():
            resolved = resolve_inside(self.workspace, str(target)).resolve()
            actual = _file_sha256(resolved) if resolved.is_file() else "missing"
            if actual != str(expected):
                self._invalidate(relation, "approved_target_drift")
                raise RepairConflictError(
                    "STALE_EVIDENCE: approved target changed before repair"
                )

    def _invalidate(self, relation: Mapping[str, object], reason: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE verification_repair_relations SET status='stale_evidence',"
                "updated_at=? WHERE id=?",
                (time.time(), relation["id"]),
            )
            self._event(
                str(relation["source_job_id"]),
                "approval_invalidated",
                {"reason": reason},
                relation_id=str(relation["id"]),
                repair_job_id=str(relation["repair_job_id"]),
            )

    def _source_job(self, source_job_id: str) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM autopilot_jobs WHERE id=?", (source_job_id,)
        ).fetchone()
        if row is None:
            raise RepairConflictError("Source verification job not found")
        return row

    @staticmethod
    def _validate_source(source: sqlite3.Row) -> None:
        if str(source["mode"]) != "read-only":
            raise RepairConflictError("Source job must be read-only")
        if str(source["status"]) not in {"blocked", "partial"}:
            raise RepairConflictError("Source job is not repairable")
        if str(source["verification_status"]) != "failed":
            raise RepairConflictError("Source verification did not fail")

    def _project_root(self, blocker: Mapping[str, Any]) -> Path:
        attempted = blocker.get("attempted_operation")
        seed = attempted.get("target", "") if isinstance(attempted, Mapping) else ""
        return resolve_project_root(
            self.workspace, seed_paths=(str(seed),), database=self.database
        )

    def _proposal(
        self, source: sqlite3.Row, result: Mapping[str, Any], project_root: Path
    ) -> RepairProposal:
        output = str(result.get("output") or "")
        redacted = redact_sensitive_text(output, known_secrets=self.known_secrets)
        redacted = redacted.replace(str(self.workspace), "/workspace")
        paths: list[str] = []
        for match in _PATH.finditer(redacted):
            candidate = project_root / match.group("path").replace("\\", "/")
            resolved = candidate.resolve()
            if resolved.is_relative_to(project_root) and resolved.exists():
                paths.append(_virtual_path(self.workspace, resolved))
        allowed_paths = tuple(dict.fromkeys(paths))
        check = str(result.get("check") or "unknown")
        code_match = _ERROR_CODE.search(redacted)
        code = code_match.group(1) if code_match else "generic"
        missing = _MISSING_MODULE.findall(redacted)
        root_key = (
            f"environment:missing:{','.join(sorted(set(missing)))}"
            if missing
            else (
                f"{check}:{code}:{allowed_paths[0] if allowed_paths else 'environment'}"
            )
        )
        project_virtual = _virtual_path(self.workspace, project_root)
        evidence_item = {
            "evidence_id": f"{source['id']}:{check}",
            "check": check,
            "command": list(result.get("command") or ()),
            "return_code": result.get("return_code"),
            "status": str(result.get("status") or "failed"),
            "duration_seconds": result.get("duration_seconds"),
            "cwd": str(result.get("cwd") or project_virtual),
            "python_executable": str(result.get("python_executable") or "project"),
            "runner_correlation_id": str(
                result.get("runner_correlation_id") or source["id"]
            ),
            "output_excerpt": redacted[-8_000:],
            "output_sha256": str(
                result.get("full_output_sha256")
                or hashlib.sha256(output.encode("utf-8")).hexdigest()
            ),
            "output_truncated": bool(result.get("output_truncated"))
            or len(redacted) > 8_000,
            "paths": list(allowed_paths),
        }
        evidence = (evidence_item,)
        context = result.get("verification_context")
        if isinstance(context, dict) and context.get("persisted_context_id"):
            context_id = str(context["persisted_context_id"])
            if not _SHA256.fullmatch(context_id):
                raise RepairConflictError("Invalid verification context reference")
            evidence_item["persisted_context_id"] = context_id
        baseline_sha256: dict[str, str] = {}
        for path in allowed_paths:
            resolved_path = resolve_inside(self.workspace, path)
            baseline_sha256[path] = (
                _file_sha256(resolved_path) if resolved_path.is_file() else "missing"
            )
        manifest = project_root / "pyproject.toml"
        if manifest.is_file():
            baseline_sha256[_virtual_path(self.workspace, manifest)] = _file_sha256(
                manifest
            )
        plan = {
            "version": 1,
            "source_job_id": str(source["id"]),
            "source_revision": int(source["checkpoint_revision"]),
            "project_root": project_virtual,
            "root_cause_key": root_key,
            "evidence_checks": [check],
            "allowed_paths": list(allowed_paths),
            "baseline_sha256": baseline_sha256,
            "expected_effect": f"The authoritative {check} result passes.",
            "verification_checks": [check],
            "repair_cycle_limit": 2,
        }
        evidence_hash = _sha(evidence)
        plan_hash = _sha(plan)
        proposal_id = hashlib.sha256(
            f"{source['id']}:{source['checkpoint_revision']}:{root_key}:"
            f"{evidence_hash}:{plan_hash}".encode()
        ).hexdigest()[:32]
        return RepairProposal(
            proposal_id,
            str(source["id"]),
            str(source["task_identity"]) if source["task_identity"] else None,
            str(source["thread_id"]),
            str(source["workspace"]),
            int(source["checkpoint_revision"]),
            project_virtual,
            root_key,
            evidence,
            evidence_hash,
            plan,
            plan_hash,
            allowed_paths,
            "awaiting_approval",
        )

    @staticmethod
    def _decode_proposal(row: sqlite3.Row) -> RepairProposal:
        return RepairProposal(
            str(row["id"]),
            str(row["source_job_id"]),
            str(row["source_task_id"]) if row["source_task_id"] else None,
            str(row["thread_id"]),
            str(row["workspace"]),
            int(row["source_revision"]),
            str(row["project_root"]),
            str(row["root_cause_key"]),
            tuple(json.loads(str(row["evidence_json"]))),
            str(row["evidence_sha256"]),
            dict(json.loads(str(row["plan_json"]))),
            str(row["plan_sha256"]),
            tuple(json.loads(str(row["allowed_paths_json"]))),
            str(row["status"]),
        )

    def _event(
        self,
        source_job_id: str,
        event: str,
        payload: Mapping[str, object],
        *,
        relation_id: str | None = None,
        repair_job_id: str | None = None,
    ) -> None:
        safe_payload = {
            key: redact_sensitive_text(str(value), known_secrets=self.known_secrets)
            for key, value in payload.items()
        }
        encoded = _canonical(safe_payload)
        self.db.execute(
            "INSERT INTO verification_repair_events("
            "relation_id,source_job_id,repair_job_id,event,payload_json,"
            "payload_sha256,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                relation_id,
                source_job_id,
                repair_job_id,
                event,
                encoded[:16_000],
                hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                time.time(),
            ),
        )


def validate_approval_hash(value: str) -> str:
    """Validate a client echo of one server-generated approval hash."""

    if not _SHA256.fullmatch(value):
        raise RepairConflictError("Approval hash must be SHA-256")
    return value
