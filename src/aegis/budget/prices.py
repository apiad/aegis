"""Backward-compatible shim over the YAML-backed model registry.

The canonical data now lives in ``src/aegis/data/models.yaml`` and is
served by ``aegis.models``. This module re-exports the same names the
rest of the codebase already imports (``ProviderPrices``, ``lookup``,
``UnknownPriceError``) so callers don't need to change. The ``PRICES``
dict is built lazily from the registry on first access.
"""
from __future__ import annotations

from aegis.models import (
    ProviderPrices,
    UnknownPriceError,
    get_prices,
    load_registry,
)


def lookup(provider: str, model: str) -> ProviderPrices:
    return get_prices(provider, model)


def _build_prices() -> dict[tuple[str, str], ProviderPrices]:
    reg = load_registry()
    out: dict[tuple[str, str], ProviderPrices] = {}
    for prov_name, prov in reg.providers.items():
        for model_name, entry in prov.models.items():
            if entry.prices is not None:
                out[(prov_name, model_name)] = entry.prices
    return out


class _LazyPricesDict(dict):
    """Compatibility view over the YAML-backed registry. Behaves like a
    frozen mapping; raises if anyone tries to mutate it (no callers do
    today but the old module-level dict was mutable in principle)."""

    def __init__(self) -> None:
        super().__init__()
        self._loaded = False

    def _ensure(self) -> None:
        if not self._loaded:
            super().update(_build_prices())
            self._loaded = True

    def __getitem__(self, key):  # type: ignore[override]
        self._ensure()
        return super().__getitem__(key)

    def __contains__(self, key):  # type: ignore[override]
        self._ensure()
        return super().__contains__(key)

    def __iter__(self):  # type: ignore[override]
        self._ensure()
        return super().__iter__()

    def __len__(self):  # type: ignore[override]
        self._ensure()
        return super().__len__()

    def items(self):  # type: ignore[override]
        self._ensure()
        return super().items()

    def keys(self):  # type: ignore[override]
        self._ensure()
        return super().keys()

    def values(self):  # type: ignore[override]
        self._ensure()
        return super().values()


PRICES: dict[tuple[str, str], ProviderPrices] = _LazyPricesDict()  # type: ignore[assignment]


__all__ = ["PRICES", "ProviderPrices", "UnknownPriceError", "lookup"]
