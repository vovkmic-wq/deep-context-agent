"""Release-aware ordering, five-choice limits, and full validation catalogs."""

import json

from context_agent.config import ProviderConfig
from context_agent.model_catalog import (
    ModelCatalog,
    enrich_release_dates,
    recent_models,
)
from context_agent.web import ProviderRegistry, _probe_openai_models


def provider():
    return ProviderConfig(
        name="lmstudio",
        model="older-configured",
        api_key="lm-studio",
        base_url="http://localhost:1234/v1",
    )


def test_release_date_wins_over_record_creation_and_unknown_is_last():
    catalog = ModelCatalog(
        [
            {"id": "a", "release_date": "2025-01-01", "created": 1700000000},
            {"id": "b", "release_date": "2024-01-01", "created": 1750000000},
            {"id": "unknown"},
            {"id": "future", "release_date": "2999-01-01"},
        ]
    )
    result = recent_models(catalog, catalog.dates)
    assert [item["id"] for item in result] == ["a", "b", "unknown", "future"]
    assert result[0]["date_basis"] == "provider_release"
    assert result[-1]["date"] is None


def test_catalog_creation_is_not_mislabeled_release():
    catalog = ModelCatalog([{"id": "m", "created": 1700000000}])
    assert catalog.dates["m"]["date_basis"] == "catalog_created"


def test_verified_release_enrichment_and_alias_deduplication():
    models = ("gpt-5.6-sol", "gpt-5.5", "gpt-5.5-2026-04-23")
    dates = enrich_release_dates("openai", models, {})
    choices = recent_models(models, dates)
    assert [m["id"] for m in choices] == ["gpt-5.6-sol", "gpt-5.5"]
    assert choices[0]["date_basis"] == "official_release"
    assert choices[0]["source"] == "https://openai.com/index/gpt-5-6/"
    assert "glm-5.3" not in dates


def test_switch_model_does_not_inherit_incompatible_reasoning_effort(monkeypatch):
    selected = ProviderConfig(
        name="openai",
        model="gpt-5.6-sol",
        api_key="test",
        base_url="https://api.openai.com/v1",
        reasoning_effort="none",
    )
    monkeypatch.setattr(
        "context_agent.web._probe_openai_models",
        lambda _: ("gpt-5.6-sol", "gpt-5.5-pro"),
    )
    registry = ProviderRegistry((selected,))
    pro = registry.snapshot_for("openai", "gpt-5.5-pro")[0]
    assert pro.reasoning_effort is None
    assert selected.reasoning_effort == "none"


def test_five_choices_and_old_active_model_remains_valid(monkeypatch):
    catalog = ModelCatalog(
        [{"id": f"model-{i}", "release_date": f"2025-01-{i:02d}"} for i in range(1, 10)]
        + [{"id": "embed-model", "release_date": "2025-12-01"}]
    )
    monkeypatch.setattr("context_agent.web._probe_openai_models", lambda _: catalog)
    registry = ProviderRegistry((provider(),))
    choices = registry.recent_model_choices("lmstudio")
    assert [item["id"] for item in choices] == [f"model-{i}" for i in range(9, 4, -1)]
    assert registry.snapshot()[0].model == "older-configured"
    assert "older-configured" not in {choice["id"] for choice in choices}
    assert registry.snapshot_for("lmstudio", "model-1")[0].model == "model-1"
    assert (
        registry.snapshot_for("lmstudio", "older-configured")[0].model
        == "older-configured"
    )


def test_probe_keeps_dates_beyond_old_first_100_cutoff(monkeypatch):
    rows = [{"id": f"m-{i}", "created": 1700000000 + i} for i in range(120)]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _size):
            return json.dumps({"data": rows}).encode()

    monkeypatch.setattr("context_agent.web.urlopen", lambda *_a, **_kw: Response())
    catalog = _probe_openai_models(provider())
    assert len(catalog) == 120
    assert isinstance(catalog, ModelCatalog)
    assert recent_models(catalog, catalog.dates)[0]["id"] == "m-119"
