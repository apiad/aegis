"""turn_end tells the agent what the person has seen this turn (#679): a model
that worked its answer out in its thinking believes it was shown, so the reply
counts the prose in the transcript since the last send."""

import json

from aegis.agent_ops import SEEN_NOTHING, SEEN_SOME
from aegis.transcript.store import read_store

from .test_agents import turn, world  # noqa: F401

END = ["turn_end", {"attention": "review", "line": "a step"}]


def script(*steps) -> str:
    return "/script " + json.dumps(list(steps))


def last_turn_end_reply(s) -> str:
    """The text of the last turn_end tool result in the session's store."""
    records, _ = read_store(s.store.path)
    calls, reply = set(), None
    for r in records:
        try:
            x = json.loads(r.get("line") or "null")
        except ValueError:
            continue
        for b in ((x or {}).get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b["name"].endswith("turn_end"):
                calls.add(b["id"])
            if b.get("type") == "tool_result" and b.get("tool_use_id") in calls:
                c = b["content"]
                reply = c if isinstance(c, str) else "".join(p["text"] for p in c)
    assert reply is not None, "no turn_end result in the store"
    return reply


async def named(world):  # noqa: F811
    s = await world.spawn()
    world.app.sessions.rename(s.log_id, "seen-test", "A named session")
    return s


async def test_nothing_written_before_the_call_asks_for_the_whole_message(world):  # noqa: F811
    s = await named(world)
    await turn(s, script(END))
    assert last_turn_end_reply(s) == f"noted. {SEEN_NOTHING}"


async def test_prose_before_the_call_is_counted_and_not_asked_again(world):  # noqa: F811
    s = await named(world)
    answer = "The bug is in volume: it squares r instead of cubing it."
    await turn(s, script(["say", answer], END))
    assert last_turn_end_reply(s) == f"noted. {SEEN_SOME.format(n=len(answer))}"


async def test_prose_from_an_earlier_turn_does_not_count(world):  # noqa: F811
    s = await named(world)
    await turn(s, script(["say", "An answer the person read last turn."], END))
    await turn(s, script(END))
    assert last_turn_end_reply(s) == f"noted. {SEEN_NOTHING}"


async def test_an_unnamed_session_hears_both_lines(world):  # noqa: F811
    s = await world.spawn()
    await turn(s, script(["say", "Done."], END))
    reply = last_turn_end_reply(s)
    assert reply.startswith(f"noted. {SEEN_SOME.format(n=5)}\n")
    assert "session_rename" in reply
