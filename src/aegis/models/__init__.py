"""Model registry — provider → model → {context_window, prices}.

The canonical data lives in ``src/aegis/data/models.yaml`` (shipped with
the package) and is mirrored at ``~/.cache/aegis/models.yaml`` after a
successful background refresh (see ``aegis.models.refresh``). The cache
wins over the bundled file when present and newer, so prices and
context windows update without a release.

Public surface:

- ``get_prices(provider, model)`` — raises ``UnknownPriceError`` on miss
  (preserves the prior ``aegis.budget.prices.lookup`` contract).
- ``get_context_window(harness, model)`` — exact match first, then the
  provider's ``context_window_patterns`` substring fallback, then the
  provider default; returns 0 for unknown providers (preserves the prior
  ``aegis.tui.metrics.context_window_for`` contract).
- ``load_registry()`` — returns the in-memory ``Registry`` dataclass.
  Cached per process; pass ``force=True`` to re-read after a refresh.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from importlib import resources
from pathlib import Path
from io import StringIO
from typing import Any

from ruamel.yaml import YAML


class UnknownPriceError(KeyError):
    """Raised when ``get_prices()`` can't find a (provider, model) pair."""


@dataclass(frozen=True)
class ProviderPrices:
    """Per-million-token rates in USD. Decimal to avoid float drift."""
    input:       Decimal
    output:      Decimal
    cache_hit:   Decimal
    cache_write: Decimal
    thinking:    Decimal


@dataclass(frozen=True)
class ContextWindowPattern:
    match: str           # lowercase substring matched against the model name
    context_window: int


@dataclass(frozen=True)
class ModelEntry:
    context_window: int | None = None
    prices: ProviderPrices | None = None
    # Alternate names the underlying CLI accepts for the same model
    # (e.g. ``claude-opus-4-7`` is an alias of ``opus``). The dropdown
    # only shows the canonical key; aliases let users hand-write the
    # explicit version in ``.aegis.yaml`` and still get correct prices
    # / context-window lookups.
    aliases: tuple[str, ...] = ()
    # Optional human-readable label shown next to the canonical name in
    # the model picker (e.g. "opus → claude-opus-4-7"). Omitted = name only.
    label: str = ""


@dataclass(frozen=True)
class ProviderEntry:
    default_context_window: int = 0
    context_window_patterns: list[ContextWindowPattern] = field(default_factory=list)
    models: dict[str, ModelEntry] = field(default_factory=dict)

    def resolve(self, model: str) -> tuple[str, ModelEntry] | None:
        """Return (canonical_name, entry) if ``model`` matches a canonical
        key or any alias; None otherwise. Used by lookups so aliases get
        the same prices / context window as the canonical entry."""
        if model in self.models:
            return model, self.models[model]
        for name, entry in self.models.items():
            if model in entry.aliases:
                return name, entry
        return None


@dataclass(frozen=True)
class Registry:
    version: int
    updated: str
    providers: dict[str, ProviderEntry]

    def get_prices(self, provider: str, model: str) -> ProviderPrices:
        prov = self.providers.get(provider)
        if prov is not None:
            resolved = prov.resolve(model)
            if resolved is not None and resolved[1].prices is not None:
                return resolved[1].prices
        raise UnknownPriceError(
            f"no price for {(provider, model)!r}; "
            f"add to src/aegis/data/models.yaml")

    def get_context_window(self, harness: str, model: str) -> int:
        prov = self.providers.get(harness)
        if prov is None:
            return 0
        resolved = prov.resolve(model)
        if resolved is not None and resolved[1].context_window is not None:
            return resolved[1].context_window
        # Pattern fallback (case-insensitive substring match, first wins).
        lower = (model or "").lower()
        for pat in prov.context_window_patterns:
            if pat.match.lower() in lower:
                return pat.context_window
        return prov.default_context_window

    def models_for(self, provider: str) -> list[tuple[str, str]]:
        """Return ``[(canonical_name, display_label), ...]`` for the
        provider's model picker — canonical-name order preserved as
        written in models.yaml. ``display_label`` is the canonical name
        plus the entry's optional ``label`` (e.g. ``"opus → claude-opus-4-7"``).
        Empty list when the provider is unknown.
        """
        prov = self.providers.get(provider)
        if prov is None:
            return []
        out: list[tuple[str, str]] = []
        for name, entry in prov.models.items():
            label = (f"{name} → {entry.label}" if entry.label else name)
            out.append((name, label))
        return out


def cache_path() -> Path:
    """Where the background-refreshed copy lands. User-level so multiple
    project checkouts share one cache."""
    return Path.home() / ".cache" / "aegis" / "models.yaml"


def _bundled_yaml_text() -> str:
    return resources.files("aegis.data").joinpath("models.yaml").read_text(
        encoding="utf-8")


def _read_source() -> str:
    """Prefer the cache when it exists and is newer than the bundled file
    (so an updated cache always wins). Otherwise fall back to bundled."""
    cache = cache_path()
    bundled = resources.files("aegis.data").joinpath("models.yaml")
    if cache.exists():
        try:
            # Treat cache as authoritative whenever it parses to a
            # mapping with a ``providers`` key — otherwise it's almost
            # certainly a partial download or unrelated text and we
            # should fall back rather than corrupt downstream lookups.
            text = cache.read_text(encoding="utf-8")
            parsed = YAML(typ="safe").load(StringIO(text))
            if not isinstance(parsed, dict) or "providers" not in parsed:
                raise ValueError("cache missing 'providers' mapping")
            return text
        except Exception:  # noqa: BLE001
            # Corrupt cache → fall through to bundled.
            pass
    return bundled.read_text(encoding="utf-8")


def _parse(text: str) -> Registry:
    raw: Any = YAML(typ="safe").load(StringIO(text))
    if not isinstance(raw, dict):
        raise ValueError("models.yaml: top-level must be a mapping")
    providers: dict[str, ProviderEntry] = {}
    for prov_name, prov_raw in (raw.get("providers") or {}).items():
        prov_raw = prov_raw or {}
        patterns = [
            ContextWindowPattern(
                match=str(p["match"]),
                context_window=int(p["context_window"]))
            for p in (prov_raw.get("context_window_patterns") or [])
        ]
        models: dict[str, ModelEntry] = {}
        for model_name, model_raw in (prov_raw.get("models") or {}).items():
            model_raw = model_raw or {}
            prices = None
            if "prices" in model_raw and model_raw["prices"]:
                pr = model_raw["prices"]
                prices = ProviderPrices(
                    input=Decimal(str(pr["input"])),
                    output=Decimal(str(pr["output"])),
                    cache_hit=Decimal(str(pr["cache_hit"])),
                    cache_write=Decimal(str(pr["cache_write"])),
                    thinking=Decimal(str(pr["thinking"])),
                )
            cw = model_raw.get("context_window")
            aliases = tuple(
                str(a) for a in (model_raw.get("aliases") or []))
            label = str(model_raw.get("label") or "")
            models[str(model_name)] = ModelEntry(
                context_window=int(cw) if cw is not None else None,
                prices=prices,
                aliases=aliases,
                label=label,
            )
        providers[str(prov_name)] = ProviderEntry(
            default_context_window=int(
                prov_raw.get("default_context_window") or 0),
            context_window_patterns=patterns,
            models=models,
        )
    return Registry(
        version=int(raw.get("version") or 1),
        updated=str(raw.get("updated") or ""),
        providers=providers,
    )


_registry: Registry | None = None


def load_registry(force: bool = False) -> Registry:
    """Return the active in-memory ``Registry``. Cached per process.

    Pass ``force=True`` to re-read from disk after a background refresh
    wrote a new cache file.
    """
    global _registry
    if _registry is None or force:
        _registry = _parse(_read_source())
    return _registry


def get_prices(provider: str, model: str) -> ProviderPrices:
    return load_registry().get_prices(provider, model)


def get_context_window(harness: str, model: str) -> int:
    return load_registry().get_context_window(harness, model)


def models_for(provider: str) -> list[tuple[str, str]]:
    """Return the model picker options for a provider — see
    ``Registry.models_for``."""
    return load_registry().models_for(provider)


__all__ = [
    "ContextWindowPattern",
    "ModelEntry",
    "ProviderEntry",
    "ProviderPrices",
    "Registry",
    "UnknownPriceError",
    "cache_path",
    "get_context_window",
    "get_prices",
    "load_registry",
    "models_for",
]
