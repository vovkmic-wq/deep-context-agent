"""Deterministic, data-aware routing for Web chat requests."""

# ruff: noqa: RUF001 -- Russian routing patterns intentionally use Cyrillic.

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

ExecutionMode = Literal["single-turn", "persistent"]
RequestedExecutionMode = Literal["auto", "autopilot", "single-turn"]
Workflow = Literal[
    "answer",
    "log-analysis",
    "targeted-review",
    "targeted-change",
    "project-audit",
    "project-change",
    "project-test",
    "plan",
    "debug",
]
Scope = Literal["message", "attachment", "file", "project"]

PROJECT_WORKFLOWS = frozenset({"project-audit", "project-change", "project-test"})

_FENCE_LINE_PATTERN = re.compile(r"^\s*(```|~~~)")
_BLOCKQUOTE_PATTERN = re.compile(r"^\s*>")
_TAGGED_DATA_PATTERN = re.compile(
    r"(?is)<(?:log|attachment|output|stdout|stderr|trace)>.*?"
    r"</(?:log|attachment|output|stdout|stderr|trace)>"
)
_DATA_START_LINE_PATTERN = re.compile(
    r"(?iu)^\s*(?:"
    r"windows\s+powershell\b|copyright\s*\(c\)|"
    r"ps\s+[a-z]:\\.*?>|traceback\s*\(most\s+recent\s+call\s+last\)|"
    r"verified\s+tool\s+operations\s*:|"
    r"(?:лог|журнал|вывод|output|stdout|stderr|stack\s+trace)\s*:"
    r")"
)
_INLINE_DATA_PATTERN = re.compile(
    r"(?isu)^(?P<instruction>.*?(?:"
    r"лог(?:а|и|ом|е)?|журнал(?:а|е|ом)?|вывод(?:а|е|ом)?|"
    r"\blog\b|\boutput\b|\btraceback\b"
    r"))\s*:\s*(?P<data>.+)$"
)
_LOG_INTENT_PATTERN = re.compile(
    r"(?iu)(?:"
    r"(?:проанализ|анализ|разбер|изуч|объясн|проверь|посмотр)"
    r"[^\n.!?]{0,80}(?:лог|журнал|вывод|ошиб)|"
    r"(?:лог|журнал|вывод|ошиб)[^\n.!?]{0,80}"
    r"(?:проанализ|разбер|объясн|почему)|"
    r"\b(?:analy[sz]e|explain|inspect|review)\b[^\n.!?]{0,80}"
    r"\b(?:log|traceback|error|output)\b"
    r")"
)
_EXPLANATION_PATTERN = re.compile(
    r"(?iu)(?:\b(?:why|what|explain|status)\b|"
    r"почему|зачем|что\s+(?:это\s+)?значит|что\s+(?:сейчас\s+)?делает|"
    r"объясн|проанализ)"
)
_PROJECT_SCOPE_PATTERN = re.compile(
    r"(?iu)(?:\b(?:project|repository|repo|codebase|workspace|all\s+files)\b|"
    r"проект|репозитор|кодов\w*\s+баз|рабоч\w*\s+(?:каталог|директор)|"
    r"все\s+файл|всю\s+директор|весь\s+каталог)"
)
_PROJECT_ARTIFACT_PATTERN = re.compile(
    r"(?iu)(?:\b(?:code|specification|requirements|tests?|modules?)\b|"
    r"\bкод(?:а|е|ом|у)?\b|технич\w*\s+задан|\bтз\b|промп?т|тест|модул)"
)
_AUDIT_PATTERN = re.compile(
    r"(?iu)(?:\b(?:audit|review|inspect|check|analy[sz]e)\b|"
    r"аудит|ревью|пров(?:ерь|ерить|ерка|ерку|ерки|ерок|еря)|"
    r"проанализ|анализ)"
)
_BROAD_SCOPE_PATTERN = re.compile(
    r"(?iu)(?:\b(?:full|complete|entire|whole|all)\b|"
    r"пол(?:ный|ностью)|целиком|весь|всю|все|кажд\w*)"
)
_MUTATION_PATTERN = re.compile(
    r"(?iu)(?:\b(?:implement|fix|repair|change|update|create|write|delete|"
    r"remove|refactor|build|deliver)\b|реализ|исправ|устран|внес\w*\s+измен|"
    r"измен|обнов|созда|удал|доработ|рефактор|довед|сделай|выполн|"
    r"разбира|заним)"
)
_TEST_PATTERN = re.compile(
    r"(?iu)(?:\b(?:test|tests|testing|pytest|ruff|mypy)\b|"
    r"тест|провер\w*\s+(?:код|проект|сборк))"
)
_PLAN_PATTERN = re.compile(r"(?iu)(?:\bplan\b|план|спланир|уточняющ)")
_PATH_PATTERN = re.compile(
    r"(?iu)(?:[a-z]:[\\/][^\s\"'<>|]+|/(?:workspace|[a-z0-9_.-]+)(?:/[^\s]+)*)"
)
_ATTACHMENT_PATTERN = re.compile(
    r"(?iu)(?:во\s+вложени|прикрепл[её]н|attachment|attached)"
)
_READ_ONLY_PATTERN = re.compile(
    r"(?iu)(?:ничего\s+не\s+(?:меняй|изменяй)|только\s+(?:анализ|чтение)|"
    r"не\s+(?:меняй|изменяй)\s+файл|без\s+измен\w*(?:\s+файл\w*)?|"
    r"read[- ]only|do\s+not\s+(?:change|modify))"
)


@dataclass(frozen=True, slots=True)
class DirectInstruction:
    """Routing-safe instruction separated from quoted or pasted data."""

    text: str
    excluded_data_chars: int


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """A safe, explainable execution decision for one chat turn."""

    execution: ExecutionMode
    workflow: Workflow
    scope: Scope
    allow_project_scan: bool
    confidence: float
    reason_codes: tuple[str, ...]
    instruction_chars: int
    excluded_data_chars: int
    mutation_requested: bool

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe payload without user or attachment content."""

        return asdict(self)


def extract_direct_instruction(query: str) -> DirectInstruction:
    """Exclude fenced, quoted, tagged, and recognizable log payloads."""

    source = query.strip()
    if not source:
        return DirectInstruction("", 0)

    tagged_removed = 0

    def remove_tagged(match: re.Match[str]) -> str:
        nonlocal tagged_removed
        tagged_removed += len(match.group(0))
        return "\n"

    remaining = _TAGGED_DATA_PATTERN.sub(remove_tagged, source)
    inline = _INLINE_DATA_PATTERN.match(remaining)
    if inline is not None and _LOG_INTENT_PATTERN.search(inline.group("instruction")):
        instruction = _normalize_instruction(inline.group("instruction"))
        return DirectInstruction(instruction, max(0, len(source) - len(instruction)))

    kept: list[str] = []
    excluded = tagged_removed
    in_fence = False
    data_started = False
    for line in remaining.splitlines(keepends=True):
        stripped = line.strip()
        if _FENCE_LINE_PATTERN.match(line):
            in_fence = not in_fence
            excluded += len(line)
            continue
        if in_fence or data_started or _BLOCKQUOTE_PATTERN.match(line):
            excluded += len(line)
            continue
        if _DATA_START_LINE_PATTERN.match(line):
            data_started = True
            excluded += len(line)
            continue
        if stripped:
            kept.append(stripped)

    instruction = _normalize_instruction("\n".join(kept))
    excluded = max(excluded, len(source) - len(instruction))
    return DirectInstruction(instruction, max(0, excluded))


def route_chat_request(
    query: str,
    *,
    work_mode: str = "agent",
    requested_execution: RequestedExecutionMode = "auto",
) -> RoutingDecision:
    """Classify only direct instructions and return an explainable route."""

    direct = extract_direct_instruction(query)
    instruction = direct.text
    log_intent = bool(_LOG_INTENT_PATTERN.search(instruction))
    mutation = bool(_MUTATION_PATTERN.search(instruction))
    read_only = bool(_READ_ONLY_PATTERN.search(instruction))
    mutation_requested = mutation and not read_only
    project_scope = bool(_PROJECT_SCOPE_PATTERN.search(instruction))
    project_artifact = bool(_PROJECT_ARTIFACT_PATTERN.search(instruction))
    audit = bool(_AUDIT_PATTERN.search(instruction))
    broad = bool(_BROAD_SCOPE_PATTERN.search(instruction))
    testing = bool(_TEST_PATTERN.search(instruction))
    paths = tuple(
        match.group(0).rstrip(".,;:!?") for match in _PATH_PATTERN.finditer(instruction)
    )
    exact_file_path = any(
        path.replace("\\", "/").rstrip("/").casefold() != "/workspace" for path in paths
    )

    scope: Scope
    if exact_file_path:
        scope = "file"
    elif project_scope or (mutation_requested and project_artifact):
        scope = "project"
    elif _ATTACHMENT_PATTERN.search(instruction):
        scope = "attachment"
    else:
        scope = "message"

    workflow: Workflow
    if work_mode == "plan":
        workflow = "plan"
    elif work_mode == "debug":
        workflow = "debug"
    elif log_intent and not (mutation_requested and scope == "project"):
        workflow = "log-analysis"
    elif scope == "project" and mutation_requested:
        workflow = "project-change"
    elif scope == "project" and audit and broad:
        workflow = "project-audit"
    elif scope == "project" and testing:
        workflow = "project-test"
    elif scope == "file" and mutation_requested:
        workflow = "targeted-change"
    elif scope == "file":
        workflow = "targeted-review"
    else:
        workflow = "answer"

    project_workflow = workflow in PROJECT_WORKFLOWS
    allow_project_scan = project_workflow or (
        scope == "project" and workflow in {"plan", "debug"}
    )
    reasons: list[str] = [f"WORKFLOW_{workflow.upper().replace('-', '_')}"]
    if direct.excluded_data_chars:
        reasons.append("QUOTED_DATA_EXCLUDED")
    if read_only:
        reasons.append("READ_ONLY_INTENT")

    if work_mode in {"ask", "plan", "debug"}:
        execution: ExecutionMode = "single-turn"
        reasons.append(f"WORK_MODE_{work_mode.upper()}_SINGLE_TURN")
    elif requested_execution == "single-turn":
        execution = "single-turn"
        reasons.append("EXPLICIT_SINGLE_TURN")
    elif requested_execution == "autopilot":
        execution = "persistent"
        reasons.append("EXPLICIT_PERSISTENT")
    elif project_workflow:
        execution = "persistent"
        reasons.append("AUTO_PROJECT_WORKFLOW")
    else:
        execution = "single-turn"
        reasons.append("AUTO_CONSERVATIVE_SINGLE_TURN")

    if log_intent and not project_workflow:
        confidence = 0.99
    elif project_workflow and (project_scope or project_artifact):
        confidence = 0.95
    elif workflow in {"plan", "debug", "targeted-review"}:
        confidence = 0.92
    elif not instruction:
        confidence = 0.70
    else:
        confidence = 0.85

    return RoutingDecision(
        execution=execution,
        workflow=workflow,
        scope=scope,
        allow_project_scan=allow_project_scan,
        confidence=confidence,
        reason_codes=tuple(reasons),
        instruction_chars=len(instruction),
        excluded_data_chars=direct.excluded_data_chars,
        mutation_requested=mutation_requested,
    )


def _normalize_instruction(value: str) -> str:
    normalized = "\n".join(line.strip() for line in value.splitlines() if line.strip())
    return normalized.rstrip(":").rstrip()
