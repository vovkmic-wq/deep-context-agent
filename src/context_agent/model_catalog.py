"""Bounded model choices with explicit, non-fabricated date provenance."""

import re
from datetime import UTC, datetime
from typing import Any

# Dates of general/API availability in the linked official release publications.
# This supplements, but never invents models missing from the provider catalog.
_ZAI_RELEASES = "https://docs.z.ai/release-notes/new-released"
_OPENAI_56_RELEASE = "https://openai.com/index/gpt-5-6/"
_OFFICIAL_RELEASES = {
    "zhipu": {
        "glm-5.3-flash": ("2026-08-26", _ZAI_RELEASES),
        "glm-5.3": ("2026-08-18", _ZAI_RELEASES),
        "glm-5.2": ("2026-06-16", _ZAI_RELEASES),
        "glm-5.1": ("2026-04-07", _ZAI_RELEASES),
        "glm-5": ("2026-02-12", _ZAI_RELEASES),
        "glm-4.7-flash": ("2026-01-19", _ZAI_RELEASES),
        "glm-4.7": ("2025-12-22", _ZAI_RELEASES),
        "glm-4.6v": ("2025-12-08", _ZAI_RELEASES),
        "glm-4.6": ("2025-09-30", _ZAI_RELEASES),
    },
    "openai": {
        "gpt-5.6-sol": ("2026-07-09", _OPENAI_56_RELEASE),
        "gpt-5.6-terra": ("2026-07-09", _OPENAI_56_RELEASE),
        "gpt-5.6-luna": ("2026-07-09", _OPENAI_56_RELEASE),
        "gpt-5.5": ("2026-04-24", "https://openai.com/index/introducing-gpt-5-5/"),
        "gpt-5.5-pro": ("2026-04-24", "https://openai.com/index/introducing-gpt-5-5/"),
    },
}


def enrich_release_dates(
    provider: str,
    ids: tuple[str, ...],
    dates: dict[str, dict[str, str | None]],
) -> dict[str, dict[str, str | None]]:
    """Apply verified exact-ID release metadata, without inferring alias dates."""
    result = {model: dict(value) for model, value in dates.items()}
    official = _OFFICIAL_RELEASES.get(provider, {})
    for model in ids:
        if (
            model in official
            and result.get(model, {}).get("date_basis") != "provider_release"
        ):
            date, source = official[model]
            parsed = _date(date)
            if parsed:
                result[model] = {
                    "date": parsed,
                    "date_basis": "official_release",
                    "source": source,
                }
    return result


class ModelCatalog(tuple):
    """Keep compatibility with model ID consumers while retaining date metadata."""

    dates: dict[str, dict[str, str | None]]

    def __new__(cls, rows: list[dict[str, Any]]) -> "ModelCatalog":
        ids = list(dict.fromkeys(row["id"].strip() for row in rows))
        instance = super().__new__(cls, ids)
        instance.dates = {}
        for row in rows:
            model = row["id"].strip()
            release = _date(row.get("released_at") or row.get("release_date"))
            created = _date(row.get("created"))
            instance.dates[model] = {
                "date": release or created,
                "date_basis": (
                    "provider_release"
                    if release
                    else "catalog_created"
                    if created
                    else "unknown"
                ),
            }
        return instance


def _date(value: Any) -> str | None:
    """Reject malformed/future dates; `created` is not a release date."""
    try:
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(value, UTC)
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
        else:
            return None
        if not parsed.year >= 2000 or parsed > datetime.now(UTC):
            return None
        return parsed.astimezone(UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def recent_models(
    ids: tuple[str, ...],
    dates: dict[str, dict[str, str | None]],
) -> list[dict[str, str | None]]:
    """Return at most five choices; stable order when dates are unavailable."""
    # A dated snapshot is not another distinct model if its public alias exists.
    distinct = tuple(
        model
        for model in ids
        if not re.search(r"-\d{4}-\d{2}-\d{2}$", model)
        or re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model) not in ids
    )
    ordered = sorted(
        distinct, key=lambda model: dates.get(model, {}).get("date") or "", reverse=True
    )
    return [
        {"id": model, **dates.get(model, {"date": None, "date_basis": "unknown"})}
        for model in ordered[:5]
    ]
