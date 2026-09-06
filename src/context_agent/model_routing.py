"""Explainable resource selection, independent of task authority and workflow."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from context_agent.errors import ConfigurationError
from context_agent.routing import extract_direct_instruction, route_chat_request

if TYPE_CHECKING:
    from context_agent.config import AppConfig, ProviderConfig


class ModelProfile(BaseModel):
    """Operator-supplied metadata; unknown measurements remain unknown."""

    model_config = ConfigDict(extra="forbid", strict=True)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    tier: Literal["fast", "standard", "reasoning"]
    tools: bool = True
    context_tokens: int = Field(ge=1024, le=10_000_000)
    input_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, gt=0)
    quality: float = Field(default=0.5, ge=0, le=1)
    enabled: bool = True

    @field_validator(
        "input_usd_per_million", "output_usd_per_million", "latency_ms", "quality"
    )
    @classmethod
    def finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Model metadata must be finite")
        return value


def parse_profiles(raw: str) -> tuple[ModelProfile, ...]:
    if not raw.strip():
        return ()
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
            raise ValueError("Expected 1..30 profiles")
        profiles = tuple(ModelProfile.model_validate(row) for row in rows)
        identities = [(p.provider, p.model, p.tier) for p in profiles]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate profile")
        return profiles
    except (ValueError, TypeError) as exc:
        # Validation errors may echo arbitrary JSON values: don't expose them.
        raise ConfigurationError("Invalid AGENT_MODEL_PROFILES JSON metadata") from exc


@dataclass(frozen=True)
class RequestSignals:
    complexity: str
    risk: str
    required_tools: bool
    tier: str


def request_signals(query: str) -> RequestSignals:
    direct = extract_direct_instruction(query).text
    route = route_chat_request(query)
    risk = (
        "high"
        if re.search(
            r"(?iu)\b(?:production|payment|authentication|migration|delete|drop|"
            r"секрет\w*|авторизац\w*|миграци\w*|удали\w*|платеж\w*|продакшн)\b",
            direct,
        )
        else "normal"
    )
    complex_request = re.search(
        r"(?iu)(?:архитектур|рефактор|распредел[её]н|многопоточ|"
        r"весь\s+проект|полност\w*\s+реализ|architecture|refactor|distributed)",
        direct,
    )
    tools = route.scope in {"file", "project"} or bool(
        re.search(
            r"(?iu)(?:найди\s+в\s+интернет|web\s+search|запусти|прочитай|read_file)",
            direct,
        )
    )
    complexity = (
        "high"
        if complex_request or len(direct) > 4000
        else "medium"
        if tools or len(direct) > 500
        else "low"
    )
    # Risk is deliberately first: a short destructive command is not a fast task.
    tier = (
        "reasoning"
        if risk == "high" or complexity == "high"
        else "fast"
        if complexity == "low" and not tools
        else "standard"
    )
    return RequestSignals(complexity, risk, tools, tier)


class NoEligibleModel(ConfigurationError):  # noqa: N818 -- resource selection signal
    """No model meets the operator's capability/privacy/budget constraints."""

    code = "model_route_unavailable"


class ModelHealth:
    """Process-local transport reliability, not semantic answer quality."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.values: dict[tuple[str, str, str], tuple[float, float, float]] = {}

    def snapshot(self, key: tuple[str, str, str]) -> tuple[float, float, float]:
        with self.lock:
            return self.values.get(key, (0, 0, 1))

    def record(
        self, key: tuple[str, str, str], success: bool, duration_ms: int
    ) -> None:
        with self.lock:
            _, latency, quality = self.values.get(key, (0, 0, 1))
            self.values[key] = (
                0 if success else time.monotonic() + 30,
                duration_ms if not latency else latency * 0.8 + duration_ms * 0.2,
                quality * 0.8 + int(success) * 0.2,
            )


MODEL_HEALTH = ModelHealth()


def is_local_provider(provider: ProviderConfig) -> bool:
    """A label alone must not permit forwarding local-only data to a remote URL."""
    hostname = urlsplit(provider.base_url).hostname or ""
    if provider.name != "lmstudio":
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class ResourceRouter:
    def __init__(self, config: AppConfig, providers: Sequence[ProviderConfig]) -> None:
        self.config = config
        self.profiles = parse_profiles(config.model_profiles)
        self.providers = tuple(providers)
        active = {p.name: p for p in providers}
        self.targets: list[ProviderConfig] = list(providers)
        self.profile_targets: list[tuple[ModelProfile, int]] = []
        for profile in self.profiles:
            if profile.provider not in active:
                continue  # Disabled providers never get re-enabled by a profile.
            base = active[profile.provider]
            existing = next(
                (
                    i
                    for i, p in enumerate(self.targets)
                    if p.name == base.name and p.model == profile.model
                ),
                None,
            )
            if existing is None:
                effort = base.reasoning_effort if base.model == profile.model else None
                self.targets.append(
                    replace(base, model=profile.model, reasoning_effort=effort)
                )
                existing = len(self.targets) - 1
            self.profile_targets.append((profile, existing))
        self.metadata: dict[str, Any] = {"mode": "configured-chain"}
        self.eligible_profiles: dict[int, ModelProfile] = {}
        self.cost_estimates: dict[int, float | None] = {}

    @staticmethod
    def health_key(provider: ProviderConfig) -> tuple[str, str, str]:
        return provider.name, provider.base_url, provider.model

    def choose(
        self,
        query: str,
        input_tokens: int,
        *,
        manual: bool = False,
        tools_used: bool = False,
        tier_override: str | None = None,
    ) -> list[int]:
        signals = request_signals(query)
        if tier_override in {"fast", "standard", "reasoning"}:
            signals = replace(signals, tier=tier_override)
        self.eligible_profiles = {}
        self.cost_estimates = {}
        if tools_used and signals.tier == "fast":
            signals = replace(signals, required_tools=True, tier="standard")
        if not self.profiles:
            self.metadata = {
                "mode": "manual" if manual else "configured-chain",
                "reason": "MANUAL_SELECTION" if manual else "PROFILES_NOT_CONFIGURED",
                "signals": asdict(signals),
                "input_tokens_estimate": input_tokens,
            }
            if self.config.model_cost_limit_usd or self.config.model_latency_budget_ms:
                raise NoEligibleModel(
                    "Budget constraints require explicit model profiles"
                )
            if self.config.model_local_only:
                indices = [
                    i for i, p in enumerate(self.providers) if is_local_provider(p)
                ]
                if not indices:
                    raise NoEligibleModel("No local model is configured")
                return indices
            return list(range(len(self.providers)))
        ranked: list[tuple[tuple[float, ...], int, float | None]] = []
        output_tokens = self.config.model_output_tokens
        for profile, index in self.profile_targets:
            provider = self.targets[index]
            if not profile.enabled:
                continue
            if manual and index >= len(self.providers):
                continue
            if not manual and profile.tier != signals.tier:
                continue
            if self.config.model_local_only and not is_local_provider(provider):
                continue
            if signals.required_tools and not profile.tools:
                continue
            if input_tokens + output_tokens > profile.context_tokens:
                continue
            cooldown, latency, reliability = MODEL_HEALTH.snapshot(
                self.health_key(provider)
            )
            if cooldown > time.monotonic():
                continue
            cost = None
            if (
                profile.input_usd_per_million is not None
                and profile.output_usd_per_million is not None
            ):
                cost = (
                    input_tokens * profile.input_usd_per_million
                    + output_tokens * profile.output_usd_per_million
                ) / 1_000_000
            if self.config.model_cost_limit_usd and (
                cost is None or cost > self.config.model_cost_limit_usd
            ):
                continue
            expected_latency = latency or profile.latency_ms
            if self.config.model_latency_budget_ms and (
                expected_latency is None
                or expected_latency > self.config.model_latency_budget_ms
            ):
                continue
            score: tuple[float, ...] = (
                -profile.quality,
                -reliability,
                cost if cost is not None else math.inf,
                expected_latency if expected_latency is not None else math.inf,
                float(index),
            )
            if manual:
                score = (float(index), *score)
            ranked.append((score, index, cost))
            self.eligible_profiles.setdefault(index, profile)
            self.cost_estimates[index] = cost
        ranked.sort()
        self.metadata = {
            "mode": "manual" if manual else "adaptive",
            "signals": asdict(signals),
            "tier": signals.tier,
            "reason": "MANUAL_SELECTION"
            if manual
            else "RISK_FIRST"
            if signals.risk == "high"
            else "COMPLEXITY_AND_TOOLS",
            "input_tokens_estimate": input_tokens,
            "estimated_cost_usd": ranked[0][2] if ranked else None,
            "eligible_candidates": len(ranked),
        }
        if not ranked:
            raise NoEligibleModel(
                f"No eligible {signals.tier} model: check profiles, tools, context, "
                "privacy, cooldown and budgets"
            )
        return list(dict.fromkeys(index for _, index, _ in ranked))

    def next_execution_tier(self, current: str) -> str | None:
        """Return the next configured tier without weakening hard filters."""

        order = ("fast", "standard", "reasoning")
        try:
            start = order.index(current)
        except ValueError:
            return None
        configured = {profile.tier for profile in self.profiles if profile.enabled}
        return next((tier for tier in order[start + 1 :] if tier in configured), None)

    def activate(self, index: int) -> None:
        """Telemetry reflects the actual attempted candidate, including fallback."""
        target = self.targets[index]
        self.metadata.update(selected_provider=target.name, selected_model=target.model)
        if index in self.cost_estimates:
            self.metadata["estimated_cost_usd"] = self.cost_estimates[index]
