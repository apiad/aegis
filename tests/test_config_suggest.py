# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""config_suggest (config_suggest.py): an agent proposes a whole new
.aegis.yaml, a person applies it from the card, and what is written is
exactly what the card's diff showed (#612)."""

from __future__ import annotations

import json

import pytest

from aegis.ops import OpError

from .conftest import until
from .test_agents import CONFIG, inbox, mcp, turn, world  # noqa: F401  (fixture)

QUEUE = "  tests: {agent: opus, max_parallel: 2}\n"
# The agents fixture's CONFIG holds a queue with no max_parallel on purpose;
# a suggestion must validate, so these tests start from a file that does.
BASE = CONFIG.replace("  broken: {agent: opus}\n", "")


@pytest.fixture(autouse=True)
def clean(world):
    (world.root / ".aegis.yaml").write_text(BASE)


def ok(said: str) -> dict:
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


def card(s, id: str) -> dict:
    return next(e for e in s.entries() if e["id"] == id)


def woken(s, needle: str) -> bool:
    return needle in json.dumps(inbox(s), ensure_ascii=False)


async def suggest(s, text: str, why: str = "a queue for tests") -> dict:
    return ok(await turn(s, mcp("config_suggest", yaml=text, why=why)))


async def apply(world, s, id: str, op: str = "apply"):
    return await world.app.registry.call(
        f"config.suggestion.{op}", {"log_id": s.log_id, "id": id}
    )


async def test_a_suggestion_records_a_card_with_its_diff(world):
    a = await world.spawn()
    got = await suggest(a, BASE + QUEUE)
    e = card(a, got["id"])
    assert e["kind"] == "config" and e["status"] == "pending"
    assert "+" + QUEUE.rstrip("\n") in e["detail"]["diff"]
    assert got["diff"] == e["detail"]["diff"]
    assert e["detail"]["why"] == "a queue for tests"
    assert (world.root / ".aegis.yaml").read_text() == BASE


async def test_apply_writes_exactly_the_suggested_text_and_wakes_the_agent(world):
    a = await world.spawn()
    text = BASE + "# kept as the agent wrote it\n" + QUEUE
    id = (await suggest(a, text))["id"]
    assert await apply(world, a, id) == "ok"
    assert (world.root / ".aegis.yaml").read_text() == text
    assert card(a, id)["status"] == "applied"
    await until(lambda: woken(a, f"config:{id} · applied"), timeout=8, what="wake")


async def test_decline_writes_nothing_and_wakes_the_agent(world):
    a = await world.spawn()
    id = (await suggest(a, BASE + QUEUE))["id"]
    assert await apply(world, a, id, "decline") == "ok"
    assert (world.root / ".aegis.yaml").read_text() == BASE
    assert card(a, id)["status"] == "declined"
    await until(lambda: woken(a, f"config:{id} · declined"), timeout=8, what="wake")


async def test_a_file_changed_since_makes_the_card_stale(world):
    a = await world.spawn()
    id = (await suggest(a, BASE + QUEUE))["id"]
    edited = BASE + "# edited by hand\n"
    (world.root / ".aegis.yaml").write_text(edited)
    with pytest.raises(OpError) as e:
        await apply(world, a, id)
    assert e.value.code == "stale"
    assert (world.root / ".aegis.yaml").read_text() == edited
    assert card(a, id)["status"] == "stale"
    await until(lambda: woken(a, f"config:{id} · stale"), timeout=8, what="wake")


async def test_two_suggestions_from_one_stamp_the_second_goes_stale(world):
    a = await world.spawn()
    first = (await suggest(a, BASE + QUEUE))["id"]
    second = (await suggest(a, BASE + "  other: {agent: opus, max_parallel: 1}\n"))[
        "id"
    ]
    await apply(world, a, first)
    with pytest.raises(OpError) as e:
        await apply(world, a, second)
    assert e.value.code == "stale"
    assert (world.root / ".aegis.yaml").read_text() == BASE + QUEUE


async def test_text_that_does_not_validate_is_refused_at_suggest(world):
    a = await world.spawn()
    before = len(a.entries())
    said = await turn(a, mcp("config_suggest", yaml="agents: [", why="w"))
    assert said.startswith("mcp error: bad_config"), said
    said = await turn(
        a, mcp("config_suggest", yaml=BASE + "  bad: {agent: nobody}\n", why="w")
    )
    assert said.startswith("mcp error: bad_config"), said
    assert not [e for e in a.entries()[before:] if e["kind"] == "config"]


async def test_a_card_applies_after_a_restart(world):
    a = await world.spawn()
    id = (await suggest(a, BASE + QUEUE))["id"]
    log_id = a.log_id
    await world.restart()
    a = world.session(log_id)
    assert a is not None and card(a, id)["status"] == "pending"
    assert await apply(world, a, id) == "ok"
    assert (world.root / ".aegis.yaml").read_text() == BASE + QUEUE


async def test_a_second_apply_is_refused(world):
    a = await world.spawn()
    id = (await suggest(a, BASE + QUEUE))["id"]
    await apply(world, a, id)
    mtime = (world.root / ".aegis.yaml").stat().st_mtime_ns
    for op in ("apply", "decline"):
        with pytest.raises(OpError) as e:
            await apply(world, a, id, op)
        assert e.value.code == "gone"
    assert (world.root / ".aegis.yaml").stat().st_mtime_ns == mtime


async def test_the_tool_is_for_agents_and_the_ops_are_not(world):
    import httpx

    async with httpx.AsyncClient() as c:
        r = await c.post(
            world.base + "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    tools = {t["name"] for t in r.json()["result"]["tools"]}
    assert "config_suggest" in tools
    assert not {"config_suggestion_apply", "config_suggestion_decline"} & tools


async def test_two_applies_at_once_write_once_and_the_card_stays_applied(world):
    import asyncio

    a = await world.spawn()
    id = (await suggest(a, BASE + QUEUE))["id"]
    got = await asyncio.gather(
        apply(world, a, id), apply(world, a, id), return_exceptions=True
    )
    assert sorted(str(type(g).__name__) for g in got) == ["OpError", "str"], got
    (err,) = [g for g in got if isinstance(g, OpError)]
    assert err.code == "gone"
    assert card(a, id)["status"] == "applied"
