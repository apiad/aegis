"""What a Claude model's tokens cost: USD per million, by model id.

Copied from https://platform.claude.com/docs/en/about-claude/pricing on
2026-10-08. Each rate is its own number rather than a multiple of the input
price, because the multiples differ by model: a cache hit is 0.1x input on most
models, 0.05x on Opus 5.5 and Sonnet 5.5 and 0.025x on Fable 5.1, and a 1-hour
cache write is 2x input, not 2x the 5-minute write (the legacy engine priced it
that way and overcharged every 1-hour write by a quarter).

A dated id (``claude-haiku-4-5-20251001``) and a context suffix
(``claude-opus-4-6[1m]``) price as the base id. A bare alias (``opus``), which
early sessions recorded, prices as that family's model of the time. Any other
unknown id has no price: callers count its tokens as unpriced rather than guess,
because charging zero and charging another model's rate are both silent.
Haiku 5.5 is left out: its rate depends on the prompt's size, which a usage
record does not carry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

_M = Decimal(1_000_000)


@dataclass(frozen=True)
class Prices:
    input: Decimal
    output: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal
    cache_read: Decimal

    def cost(
        self, *, inp: int, out: int, cc5: int, cc1: int, cache_read: int
    ) -> Decimal:
        """USD for one call's tokens."""
        return (
            inp * self.input
            + out * self.output
            + cc5 * self.cache_write_5m
            + cc1 * self.cache_write_1h
            + cache_read * self.cache_read
        ) / _M


def _p(inp: str, out: str, w5: str, w1: str, read: str) -> Prices:
    return Prices(Decimal(inp), Decimal(out), Decimal(w5), Decimal(w1), Decimal(read))


_FABLE_5_1 = _p("10", "50", "12.50", "20", "0.25")
_FABLE_5 = _p("10", "50", "12.50", "20", "1")
_OPUS_5_5 = _p("4", "20", "5", "8", "0.20")
_OPUS_5 = _p("5", "25", "6.25", "10", "0.50")
_OPUS_4_1 = _p("15", "75", "18.75", "30", "1.50")
_SONNET_5_5 = _p("2", "10", "2.50", "4", "0.10")
_SONNET_5 = _p("2", "10", "2.50", "4", "0.20")
_SONNET_4 = _p("3", "15", "3.75", "6", "0.30")
_HAIKU_4_5 = _p("1", "5", "1.25", "2", "0.10")
_HAIKU_3_5 = _p("0.80", "4", "1", "1.60", "0.08")

PRICES: dict[str, Prices] = {
    "claude-fable-5-1": _FABLE_5_1,
    "claude-mythos-5-1": _FABLE_5_1,
    "claude-fable-5": _FABLE_5,
    "claude-mythos-5": _FABLE_5,
    "claude-opus-5-5": _OPUS_5_5,
    "claude-opus-5": _OPUS_5,
    "claude-opus-4-8": _OPUS_5,
    "claude-opus-4-7": _OPUS_5,
    "claude-opus-4-6": _OPUS_5,
    "claude-opus-4-5": _OPUS_5,
    "claude-opus-4-1": _OPUS_4_1,
    "claude-opus-4": _OPUS_4_1,
    "claude-sonnet-5-5": _SONNET_5_5,
    "claude-sonnet-5": _SONNET_5,
    "claude-sonnet-4-6": _SONNET_4,
    "claude-sonnet-4-5": _SONNET_4,
    "claude-sonnet-4": _SONNET_4,
    "claude-haiku-4-5": _HAIKU_4_5,
    "claude-haiku-3-5": _HAIKU_3_5,
}

# Bare aliases, as the model of their family when sessions recorded them.
ALIASES = {
    "fable": "claude-fable-5",
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}

# Claude Code's own placeholder for a message no API call produced.
FREE = "<synthetic>"
_ZERO = _p("0", "0", "0", "0", "0")

_DATED = re.compile(r"-\d{8}$")


def prices_for(model: str | None) -> Prices | None:
    """The rates for ``model``, or None when it has none here."""
    if not model:
        return None
    if model == FREE:
        return _ZERO
    name = model.split("[", 1)[0].strip().lower()
    name = ALIASES.get(name, name)
    return PRICES.get(name) or PRICES.get(_DATED.sub("", name))
