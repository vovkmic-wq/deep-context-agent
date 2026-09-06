"""Golden-corpus metrics must measure semantic and exact-code retrieval."""

from context_agent.retrieval_eval import GoldenQuery, evaluate_retrieval


def test_retrieval_metrics_split_natural_and_lexical_queries() -> None:
    rankings = {
        "как получить впн": ["gym.md", "vpn.md", "equipment.md"],
        "ошибка CR-12345": ["incidents.md", "vpn.md"],
    }
    cases = [
        GoldenQuery(
            "как получить впн",
            frozenset({"vpn.md"}),
            category="natural",
        ),
        GoldenQuery(
            "ошибка CR-12345",
            frozenset({"incidents.md"}),
            category="lexical",
        ),
    ]

    metrics = evaluate_retrieval(
        cases,
        lambda query, limit: rankings[query][:limit],
    )

    assert metrics["all"].hit_at == {1: 0.5, 3: 1.0, 5: 1.0}
    assert metrics["all"].mrr == 0.75
    assert metrics["natural"].mrr == 0.5
    assert metrics["lexical"].mrr == 1.0


def test_retrieval_metrics_validate_corpus_and_cutoffs() -> None:
    try:
        evaluate_retrieval([], lambda _query, _limit: [])
    except ValueError as exc:
        assert "empty" in str(exc)
    else:  # pragma: no cover - explicit assertion message
        raise AssertionError("Empty golden corpus must be rejected")
