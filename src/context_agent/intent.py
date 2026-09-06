"""Bounded direct-command intent resolution; model output is never authority."""

# ruff: noqa: RUF001 -- bilingual continuation grammar

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from context_agent.routing import extract_direct_instruction, route_chat_request


class SemanticIntent(BaseModel):
    """Only an intent and an existing opaque identity can cross the boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["resume_task", "new_task", "side_question", "clarify"]
    task_id: str | None = None
    confidence: float = Field(ge=0, le=1)


@dataclass(frozen=True)
class IntentDecision:
    action: str
    task_id: str | None = None
    confidence: float = 1.0
    source: str = "rules"
    reason: str = "DIRECT_COMMAND"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


SemanticClassifier = Callable[[str, Sequence[str]], SemanticIntent]

_RESUME_HEAD = re.compile(
    r"(?iu)^(?:пожалуйста[,\s]+)?(?:продолж(?:и|ай|ить)|возобнови|"
    r"вернись|верн[её]мся|давай\s+(?:продолжим|верн[её]мся)|"
    r"выполни\s+следующий\s+(?:пункт|шаг)|continue|resume|next\s+step)\b"
)
_SAFE_RESUME = re.compile(
    r"(?iu)^(?:(?:выполнение|работу|разработку|реализацию|задачи|задания|задачу|"
    r"работы|задачей|задаче|над|к|с|того|места|где|ты|остановился|остановились|"
    r"предыдущей|предыдущую|в|соответствии|по|согласно|тз|и|промп?том|промп?ту|"
    r"плану|плана|реализации|учитывая|предыдущие|замечания|дальше|пожалуйста|"
    r"но|ничего|не|меняй|изменяй|только|чтение|без|изменений|"
    r"the|task|work|from|where|you|stopped|according|to|spec|plan)\b|[\s,.!—-])*$"
)
_REFERENCE = re.compile(
    r"(?iu)(?:следующ\w*\s+(?:пункт|шаг)|остановил|продолж|возобнов|"
    r"верн[её]мся|вернись|предыдущ\w*\s+задач|оставш\w*\s+част|"
    r"неокончен|unfinished|resume|continue|pick\s+up|carry\s+on|where\s+we\s+left)"
)
_QUESTION = re.compile(
    r"(?iu)^(?:почему|зачем|как|что|какие|расскажи|объясни|покажи|"
    r"есть\s+ли|на\s+какой|не|why|what|how|explain|show|don't|do\s+not)\b"
)
_NEW_TASK_HEAD = re.compile(
    r"(?iu)^(?:реализуй|создай|исправь|напиши|внеси|измени|удали|"
    r"выполни\s+(?:изменение|новую)|implement|create|fix|write|delete)\b"
)
_REFERENCE_DATA = re.compile(
    r"(?iu)(?:[a-z]:[\\/]|/)[^\s\"'<>]+|\b[\w.-]+\.[a-z0-9]{1,16}\b"
)


def resolve_intent(
    query: str,
    candidates: Sequence[str] = (),
    *,
    selected_task_id: str | None = None,
    semantic: SemanticClassifier | None = None,
) -> IntentDecision:
    """Resolve a reference, failing closed rather than inferring a new audit."""
    direct = extract_direct_instruction(query).text.strip()
    route = route_chat_request(query)
    if not direct or route.workflow == "log-analysis" or _QUESTION.search(direct):
        return IntentDecision("side_question", reason="SIDE_QUESTION")
    head = _RESUME_HEAD.match(direct)
    if head and _SAFE_RESUME.fullmatch(direct[head.end() :]):
        return IntentDecision("resume_task", selected_task_id, reason="RESUME_COMMAND")
    if not head and _NEW_TASK_HEAD.match(direct) and route.mutation_requested:
        # A new task may describe a future continuation. That embedded future
        # step must not replace the current imperative.
        return IntentDecision("new_task", reason="EXPLICIT_NEW_TASK")
    # An explicit UI selection is a disambiguator, not permission to turn a new
    # command or quoted text into a continuation.
    reference_prose = _REFERENCE_DATA.sub(" ", direct)
    if not head and not _REFERENCE.search(reference_prose):
        return IntentDecision("new_task")
    if semantic is None:
        return IntentDecision("clarify", reason="AMBIGUOUS_REFERENCE")
    try:
        result = semantic(direct[:4000], tuple(candidates[:20]))
        if not isinstance(result, SemanticIntent):
            result = SemanticIntent.model_validate(result)
    except Exception:
        return IntentDecision("clarify", source="semantic", reason="CLASSIFIER_FAILED")
    if result.confidence < 0.85:
        return IntentDecision("clarify", source="semantic", reason="LOW_CONFIDENCE")
    chosen = selected_task_id or result.task_id
    if result.task_id is not None and result.task_id not in candidates:
        return IntentDecision("clarify", source="semantic", reason="INVALID_TASK_ID")
    if selected_task_id and result.task_id and selected_task_id != result.task_id:
        return IntentDecision(
            "clarify", source="semantic", reason="CONFLICTING_TASK_ID"
        )
    # A vague reference is never allowed to grant new-task authority through LLM.
    action = (
        result.action
        if result.action in {"resume_task", "side_question"}
        else "clarify"
    )
    return IntentDecision(
        action, chosen, result.confidence, "semantic", "SEMANTIC_INTENT"
    )
