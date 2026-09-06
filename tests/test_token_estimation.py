from __future__ import annotations

from context_agent.token_estimation import estimate_input_tokens


def test_token_estimate_is_not_raw_utf8_byte_count() -> None:
    payload = {"message": "Привет мир " * 1000, "code": "value = 1\n" * 1000}

    estimate = estimate_input_tokens(payload, model="unknown-model")
    raw_bytes = len(str(payload).encode("utf-8"))

    assert estimate.tokens > estimate.content_tokens
    assert estimate.reserve_tokens >= 512
    assert estimate.tokens < raw_bytes
    assert estimate.confidence in {"low", "medium", "high"}


def test_token_estimate_is_deterministic_and_json_aware() -> None:
    left = estimate_input_tokens({"b": 2, "a": 1}, reserve_tokens=0)
    right = estimate_input_tokens({"a": 1, "b": 2}, reserve_tokens=0)

    assert left == right
    assert left.tokens >= 1
