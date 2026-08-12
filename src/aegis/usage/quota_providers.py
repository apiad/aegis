"""The provider registry — the one place that knows every quota vendor.

Its own module rather than a table in ``quota.py`` because the provider modules
import the core, so the core cannot import them back.
"""
from __future__ import annotations

from aegis.usage.quota import QuotaProvider, QuotaService
from aegis.usage.quota_claude import PROVIDER as CLAUDE
from aegis.usage.quota_opencode import PROVIDER as OPENCODE_GO

PROVIDERS: tuple[QuotaProvider, ...] = (CLAUDE, OPENCODE_GO)


def for_harness(slug: str) -> QuotaProvider | None:
    """The provider whose quota a pane on ``slug`` spends, if any.

    Used to route a turn-end refresh at the number that just moved — not to
    decide what the bar shows.
    """
    for provider in PROVIDERS:
        if provider.harness == slug:
            return provider
    return None


def build_services() -> dict[str, QuotaService]:
    """One poller per provider, keyed by provider name."""
    return {
        p.name: QuotaService(fetch=p.fetch, token_reader=p.read_token)
        for p in PROVIDERS
    }
