"""Opt-in read-only catalog acceptance using existing provider credentials."""

import json
from pathlib import Path

from dotenv import load_dotenv

from context_agent.config import ProviderConfig
from context_agent.web import ProviderRegistry


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env.local", override=False)
    load_dotenv(root / ".env", override=False)
    providers = ProviderConfig.priority_from_env()
    registry = ProviderRegistry(providers)
    for provider in providers:
        try:
            catalog = registry.models(provider.name, refresh=True)
            choices = registry.recent_model_choices(provider.name)
            assert 1 <= len(choices) <= min(5, len(catalog))
            assert (
                registry.snapshot()[providers.index(provider)].model == provider.model
            )
            print(
                json.dumps(
                    {
                        "provider": provider.name,
                        "catalog_size": len(catalog),
                        "choices": choices,
                        "result": "PASS",
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as exc:
            # Never print raw network errors or credentials.
            print(
                json.dumps(
                    {
                        "provider": provider.name,
                        "result": "UNAVAILABLE",
                        "error_type": type(exc).__name__,
                    }
                )
            )
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
