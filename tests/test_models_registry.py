"""Model registry — YAML loader + cache precedence + lookup semantics.

Covers the migration from hardcoded ``aegis.budget.prices.PRICES`` +
``aegis.tui.metrics.context_window_for`` to the YAML-backed
``aegis.models`` registry.
"""
from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

import aegis.models as models
from aegis.models import (
    ProviderPrices,
    Registry,
    UnknownPriceError,
    get_context_window,
    get_prices,
    load_registry,
)


@pytest.fixture(autouse=True)
def _reset_registry_singleton(monkeypatch, tmp_path):
    """Each test gets a fresh singleton + a per-test cache dir so the
    user's real ~/.cache/aegis/models.yaml is never read or written."""
    monkeypatch.setattr(models, "_registry", None)
    fake_cache = tmp_path / "cache" / "models.yaml"
    monkeypatch.setattr(models, "cache_path", lambda: fake_cache)
    yield
    monkeypatch.setattr(models, "_registry", None)


# --- bundled YAML smoke -----------------------------------------------

def test_bundled_yaml_loads_with_known_providers():
    reg = load_registry()
    assert reg.version == 1
    assert "claude-code" in reg.providers
    assert "gemini" in reg.providers
    assert "opencode" in reg.providers


# --- prices --------------------------------------------------------

def test_get_prices_claude_opus_matches_prior_constants():
    p = get_prices("claude-code", "opus")
    assert p.input == Decimal("15.00")
    assert p.output == Decimal("75.00")
    assert p.cache_hit == Decimal("1.50")
    assert p.cache_write == Decimal("18.75")
    assert p.thinking == Decimal("75.00")
    assert isinstance(p, ProviderPrices)


def test_get_prices_unknown_pair_raises_unknown_price_error():
    with pytest.raises(UnknownPriceError, match="no price for"):
        get_prices("made-up-provider", "made-up-model")


def test_legacy_PRICES_dict_proxies_through_registry():
    """``aegis.budget.prices.PRICES`` is a compatibility view over the
    registry — exists so existing imports keep working."""
    from aegis.budget.prices import PRICES
    p = PRICES[("claude-code", "sonnet")]
    assert p.input == Decimal("3.00")


# --- context windows --------------------------------------------------

def test_get_context_window_exact_model_match():
    assert get_context_window("claude-code", "opus") == 1_000_000
    assert get_context_window("claude-code", "sonnet") == 200_000
    assert get_context_window("claude-code", "haiku") == 200_000


def test_get_context_window_pattern_fallback_opus_substring():
    """A model name not in the explicit list, but containing 'opus'."""
    assert get_context_window("claude-code", "claude-opus-4-7") == 1_000_000


def test_get_context_window_pattern_fallback_1m_suffix():
    assert get_context_window("claude-code", "sonnet-1m") == 1_000_000
    assert get_context_window("claude-code", "claude-sonnet-4-5-1m") == 1_000_000


def test_get_context_window_default_for_unknown_model():
    """No exact match, no pattern hit → provider default."""
    assert get_context_window("claude-code", "claude-sonnet-4-6") == 200_000
    assert get_context_window("opencode", "anthropic/claude-sonnet-4.5") == 200_000


def test_get_context_window_unknown_provider_returns_zero():
    assert get_context_window("madeup-harness", "anything") == 0


# --- cache precedence -------------------------------------------------

_MINIMAL_CACHE_YAML = """\
version: 1
updated: "2030-01-01"
providers:
  claude-code:
    default_context_window: 999999
    models:
      opus:
        context_window: 2000000
        prices:
          input:       "99.00"
          output:      "199.00"
          cache_hit:   "9.90"
          cache_write: "99.00"
          thinking:    "199.00"
"""


def test_cache_overrides_bundled_when_present(monkeypatch, tmp_path):
    """A well-formed cache file wins over the bundled copy. This is the
    update path: a 24h refresh writes a new cache and the next process
    boot picks it up automatically."""
    cache = tmp_path / "cache" / "models.yaml"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(_MINIMAL_CACHE_YAML, encoding="utf-8")
    monkeypatch.setattr(models, "cache_path", lambda: cache)
    monkeypatch.setattr(models, "_registry", None)
    p = get_prices("claude-code", "opus")
    assert p.input == Decimal("99.00")  # cache value, not bundled 15.00
    assert get_context_window("claude-code", "opus") == 2_000_000


def test_corrupt_cache_falls_back_to_bundled(monkeypatch, tmp_path):
    """A truncated / malformed cache file does NOT crash startup — the
    bundled copy keeps working until the next refresh fixes it."""
    cache = tmp_path / "cache" / "models.yaml"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("this is not yaml :::", encoding="utf-8")
    monkeypatch.setattr(models, "cache_path", lambda: cache)
    monkeypatch.setattr(models, "_registry", None)
    # Bundled price still answers.
    assert get_prices("claude-code", "opus").input == Decimal("15.00")


# --- refresh ----------------------------------------------------------

def test_maybe_refresh_skips_when_cache_is_fresh(tmp_path, monkeypatch):
    from aegis.models import refresh

    cache = tmp_path / "cache" / "models.yaml"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(_MINIMAL_CACHE_YAML, encoding="utf-8")
    monkeypatch.setattr(models, "cache_path", lambda: cache)
    refresh._reset_fired_for_tests()

    with patch.object(refresh, "_fetch_and_write") as fake:
        spawned = refresh.maybe_refresh(ttl_seconds=24 * 3600)
    assert spawned is False
    fake.assert_not_called()


def test_maybe_refresh_spawns_when_cache_is_stale(tmp_path, monkeypatch):
    from aegis.models import refresh

    cache = tmp_path / "cache" / "models.yaml"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(_MINIMAL_CACHE_YAML, encoding="utf-8")
    # Backdate so it's stale.
    old = time.time() - (48 * 3600)
    import os
    os.utime(cache, (old, old))
    monkeypatch.setattr(models, "cache_path", lambda: cache)
    refresh._reset_fired_for_tests()

    spawned_threads: list = []

    class _StubThread:
        def __init__(self, target, args, name, daemon):
            self._target = target
            self._args = args
            spawned_threads.append(self)

        def start(self):
            pass

    with patch.object(refresh.threading, "Thread", _StubThread):
        spawned = refresh.maybe_refresh(ttl_seconds=24 * 3600)
    assert spawned is True
    assert len(spawned_threads) == 1


def test_maybe_refresh_idempotent_within_process(tmp_path, monkeypatch):
    """Two boots in the same process must not double-fire (the second
    call is a no-op even when the cache is stale)."""
    from aegis.models import refresh

    cache = tmp_path / "cache" / "models.yaml"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("x", encoding="utf-8")  # missing/stale triggers fire
    old = time.time() - (48 * 3600)
    import os
    os.utime(cache, (old, old))
    monkeypatch.setattr(models, "cache_path", lambda: cache)
    refresh._reset_fired_for_tests()

    with patch.object(refresh.threading, "Thread") as fake_t:
        fake_t.return_value.start = lambda: None
        first = refresh.maybe_refresh()
        second = refresh.maybe_refresh()
    assert first is True
    assert second is False
    assert fake_t.call_count == 1


def test_fetch_and_write_validates_and_atomically_replaces(tmp_path, monkeypatch):
    """The fetcher must validate the response is a parseable models.yaml
    before overwriting the cache (so a 404 HTML body or a partial
    download never corrupts the local copy)."""
    from aegis.models import refresh

    dest = tmp_path / "models.yaml"
    dest.write_text("existing content\n", encoding="utf-8")  # initial state

    class _Resp:
        text = _MINIMAL_CACHE_YAML

        def raise_for_status(self):
            pass

    monkeypatch.setattr(refresh.httpx, "get", lambda *a, **kw: _Resp())
    refresh._fetch_and_write("https://example.invalid/x.yaml", dest)
    # Cache now matches the upstream body.
    assert "99.00" in dest.read_text(encoding="utf-8")


def test_fetch_and_write_rejects_html_body_keeps_cache(tmp_path, monkeypatch):
    from aegis.models import refresh

    dest = tmp_path / "models.yaml"
    dest.write_text(_MINIMAL_CACHE_YAML, encoding="utf-8")

    class _Resp:
        text = "<html><body>404</body></html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(refresh.httpx, "get", lambda *a, **kw: _Resp())
    refresh._fetch_and_write("https://example.invalid/x.yaml", dest)
    # Cache is untouched.
    assert "99.00" in dest.read_text(encoding="utf-8")
