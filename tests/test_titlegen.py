"""Auto-titling: the one-shot call, and its contract of never mattering.

Generation is best-effort by construction. A title is a convenience; the
conversation it labels is not. So every failure mode here asserts the same
thing — the caller gets "" and nothing raises — because the alternative is
a cosmetic feature able to take down a turn.
"""
from __future__ import annotations

import pytest

from aegis.drivers.oneshot import Generation
from aegis.titlegen import TitleSuggestion, suggest_title, title_for


class _Driver:
    """A oneshot-capable driver returning whatever it was handed."""

    supports_oneshot = True

    def __init__(self, result=None, raises: Exception | None = None):
        self._result = result
        self._raises = raises
        self.calls: list[tuple] = []

    async def generate_detailed(self, agent, cwd, schema, *instructions):
        self.calls.append((agent, cwd, schema, instructions))
        if self._raises is not None:
            raise self._raises
        return self._result if self._result is not None else Generation()


def _gen(title: str) -> Generation:
    return Generation(value=TitleSuggestion(title=title), model="haiku",
                      duration_ms=900, cost_usd=0.004)


@pytest.mark.asyncio
async def test_suggest_title_returns_the_generated_title():
    drv = _Driver(_gen("fix the eviction race"))
    out = await suggest_title(opening="the cache evicts too early, look",
                              driver=drv, agent=object(), cwd="/tmp")
    assert out == "fix the eviction race"


@pytest.mark.asyncio
async def test_the_opening_message_reaches_the_model():
    drv = _Driver(_gen("x"))
    await suggest_title(opening="please fix the geocoder",
                        driver=drv, agent=object(), cwd="/tmp")
    instructions = " ".join(drv.calls[0][3])
    assert "please fix the geocoder" in instructions


@pytest.mark.asyncio
async def test_a_generated_title_is_sanitized():
    drv = _Driver(_gen('  "Fix The Eviction Race"\nand more  '))
    out = await suggest_title(opening="x", driver=drv, agent=object(),
                              cwd="/tmp")
    assert out == "Fix The Eviction Race"


@pytest.mark.asyncio
async def test_an_overlong_title_is_capped():
    drv = _Driver(_gen("a really quite absurdly long title the model "
                       "invented despite being told not to"))
    out = await suggest_title(opening="x", driver=drv, agent=object(),
                              cwd="/tmp")
    assert len(out) <= 32


@pytest.mark.asyncio
async def test_a_driver_that_raises_yields_no_title():
    drv = _Driver(raises=RuntimeError("the endpoint is having a bad day"))
    out = await suggest_title(opening="x", driver=drv, agent=object(),
                              cwd="/tmp")
    assert out == ""


@pytest.mark.asyncio
async def test_an_unparseable_payload_yields_no_title():
    drv = _Driver(Generation())          # value=None: nothing usable
    out = await suggest_title(opening="x", driver=drv, agent=object(),
                              cwd="/tmp")
    assert out == ""


@pytest.mark.asyncio
async def test_an_empty_opening_message_skips_the_call_entirely():
    drv = _Driver(_gen("should not happen"))
    out = await suggest_title(opening="   ", driver=drv, agent=object(),
                              cwd="/tmp")
    assert out == ""
    assert drv.calls == []


@pytest.mark.asyncio
async def test_regeneration_is_told_the_previous_title():
    drv = _Driver(_gen("now about the indexer"))
    out = await suggest_title(opening="…transcript tail…", previous="old one",
                              driver=drv, agent=object(), cwd="/tmp")
    instructions = " ".join(drv.calls[0][3])
    assert "old one" in instructions
    assert out == "now about the indexer"


@pytest.mark.asyncio
async def test_title_for_declines_a_driver_without_oneshot(monkeypatch):
    class _NoOneshot:
        supports_oneshot = False

    monkeypatch.setattr("aegis.drivers.get_driver", lambda h: _NoOneshot())
    out = await title_for(opening="x", agent=_FakeAgent(), agents={},
                          cwd="/tmp")
    assert out == ""


@pytest.mark.asyncio
async def test_title_for_survives_an_unknown_harness(monkeypatch):
    def _boom(h):
        raise KeyError(h)

    monkeypatch.setattr("aegis.drivers.get_driver", _boom)
    out = await title_for(opening="x", agent=_FakeAgent(), agents={},
                          cwd="/tmp")
    assert out == ""


class _FakeAgent:
    harness = "claude-code"
    model = "claude-sonnet-4-5"
