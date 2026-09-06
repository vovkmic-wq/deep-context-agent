"""Deterministic retrieval metrics for small operator-owned golden corpora."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GoldenQuery:
    """One query with acceptable source identifiers and a stable category."""

    query: str
    expected_sources: frozenset[str]
    category: str = "natural"

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("Golden query cannot be empty")
        if not self.expected_sources:
            raise ValueError("Golden query requires at least one expected source")
        if self.category not in {"natural", "lexical"}:
            raise ValueError("Golden category must be natural or lexical")


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    """Hit ratios and reciprocal rank for one category or the full corpus."""

    queries: int
    hit_at: dict[int, float]
    mrr_at: int
    mrr: float

    def as_dict(self) -> dict[str, object]:
        return {
            "queries": self.queries,
            "hit_at": {str(key): value for key, value in self.hit_at.items()},
            "mrr_at": self.mrr_at,
            "mrr": self.mrr,
        }


def evaluate_retrieval(
    cases: Sequence[GoldenQuery],
    retrieve: Callable[[str, int], Iterable[str]],
    *,
    hit_k: tuple[int, ...] = (1, 3, 5),
    mrr_k: int = 10,
) -> dict[str, RetrievalMetrics]:
    """Evaluate actual ordered source IDs without invoking an LLM judge."""

    if not cases:
        raise ValueError("Golden corpus cannot be empty")
    if not hit_k or any(value < 1 for value in hit_k) or mrr_k < 1:
        raise ValueError("Metric cutoffs must be positive")
    maximum = max(*hit_k, mrr_k)
    raw: dict[str, list[tuple[tuple[bool, ...], float]]] = defaultdict(list)
    for case in cases:
        sources = tuple(
            dict.fromkeys(str(item) for item in retrieve(case.query, maximum))
        )
        rank = next(
            (
                index
                for index, source in enumerate(sources[:mrr_k], start=1)
                if source in case.expected_sources
            ),
            None,
        )
        hits = tuple(
            any(source in case.expected_sources for source in sources[:cutoff])
            for cutoff in hit_k
        )
        result = (hits, 0.0 if rank is None else 1.0 / rank)
        raw["all"].append(result)
        raw[case.category].append(result)

    metrics: dict[str, RetrievalMetrics] = {}
    for category, rows in raw.items():
        count = len(rows)
        metrics[category] = RetrievalMetrics(
            queries=count,
            hit_at={
                cutoff: sum(int(row[0][index]) for row in rows) / count
                for index, cutoff in enumerate(hit_k)
            },
            mrr_at=mrr_k,
            mrr=sum(row[1] for row in rows) / count,
        )
    return metrics
