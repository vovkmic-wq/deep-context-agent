"""Local, privacy-preserving input-token estimates for model routing."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    """A bounded estimate with enough metadata to explain routing decisions."""

    tokens: int
    content_tokens: int
    reserve_tokens: int
    method: str
    confidence: str

    def as_dict(self) -> dict[str, int | str]:
        return asdict(self)


def estimate_input_tokens(
    value: Any,
    *,
    model: str = "",
    reserve_tokens: int = 512,
) -> TokenEstimate:
    """Estimate a serialized request locally, using tiktoken when available.

    The fallback intentionally combines character and UTF-8 byte bounds.  It is
    conservative for Russian prose and source code without treating every byte
    as a token, which previously rejected otherwise eligible context windows.
    """

    serialized = json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    content_tokens: int
    method = "unicode-byte-bound-v1"
    confidence = "low"
    try:
        if os.getenv("AGENT_TOKEN_ESTIMATOR", "local").casefold() != "tiktoken":
            raise ModuleNotFoundError
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model)
            method = f"tiktoken:{encoding.name}"
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
            method = "tiktoken:cl100k_base-fallback"
        content_tokens = len(encoding.encode(serialized, disallowed_special=()))
        confidence = "high" if model and "fallback" not in method else "medium"
    except Exception:  # optional tokenizer/cache/network failures use local fallback
        characters = len(serialized)
        utf8_bytes = len(serialized.encode("utf-8"))
        # Roughly two Unicode characters or three UTF-8 bytes per token is a
        # safe local planning bound for mixed Russian/code payloads.
        content_tokens = max(
            math.ceil(characters / 2),
            math.ceil(utf8_bytes / 3),
            1,
        )

    uncertainty = math.ceil(content_tokens * (0.08 if confidence == "high" else 0.2))
    effective_reserve = max(0, reserve_tokens) + uncertainty
    return TokenEstimate(
        tokens=content_tokens + effective_reserve,
        content_tokens=content_tokens,
        reserve_tokens=effective_reserve,
        method=method,
        confidence=confidence,
    )
