"""`/peer` — the command surface over `AppBridge.peer_ask`."""
from __future__ import annotations

import pytest

from aegis.commands import CommandContext, dispatch
from aegis.peer import PeerAnswer


class FakeInfo:
    def __init__(self, handle, slug="claude", state="ready"):
        self.handle, self.agent_slug, self.state = handle, slug, state
        self.active = False
        self.unseen = False


class FakeBridge:
    def __init__(self, answer=None, sessions=()):
        self.answer = answer or PeerAnswer(
            answer="the build is green", target="beta", ok=True,
            model="opus", duration_ms=8200, cost_usd=0.0410)
        self.calls: list = []
        self._sessions = list(sessions)

    def list_sessions(self):
        return self._sessions

    async def peer_ask(self, from_handle, target, prompt, *, cc=False):
        self.calls.append((from_handle, target, prompt, cc))
        return self.answer


async def run(text, bridge=None, handle="alpha"):
    b = bridge or FakeBridge()
    return await dispatch(text, CommandContext(bridge=b, handle=handle)), b


@pytest.mark.asyncio
async def test_peer_passes_source_target_and_question_through():
    res, b = await run("/peer beta is the build green?")
    assert res.ok
    assert b.calls == [("alpha", "beta", "is the build green?", False)]


@pytest.mark.asyncio
async def test_the_answer_rides_the_effect_channel_as_a_plain_dict():
    # The web seam ships `effect` straight out as JSON — a dataclass here
    # would break /peer on the web client and nowhere else, which is the
    # exact bug /btw hit.
    res, _ = await run("/peer beta hi")
    assert res.effect["kind"] == "peer_answer"
    assert isinstance(res.effect["answer"], dict)
    assert res.effect["answer"]["target"] == "beta"


@pytest.mark.asyncio
async def test_the_question_survives_verbatim_including_flags():
    _, b = await run("/peer beta why did --strict-mcp-config change?")
    assert b.calls[0][2] == "why did --strict-mcp-config change?"


@pytest.mark.asyncio
async def test_a_bare_peer_is_a_usage_error_not_a_send():
    res, b = await run("/peer")
    assert not res.ok and "usage" in res.title
    assert b.calls == []


@pytest.mark.asyncio
async def test_a_peer_with_no_question_does_not_send():
    res, b = await run("/peer beta")
    assert not res.ok and b.calls == []


@pytest.mark.asyncio
async def test_a_refusal_surfaces_as_a_failed_command():
    b = FakeBridge(answer=PeerAnswer(
        target="beta", error="beta is mid-turn. Wait for it to finish, "
                             "or /enqueue the task instead."))
    res, _ = await run("/peer beta hi", bridge=b)
    assert not res.ok
    assert "mid-turn" in res.body and "/enqueue" in res.body


def test_the_completer_marks_busy_peers():
    from aegis.commands.builtins.core import _peer_targets
    b = FakeBridge(sessions=[FakeInfo("beta"),
                             FakeInfo("gamma", state="working")])
    got = dict(_peer_targets(b))
    assert got["beta"] == "claude"
    assert "busy" in got["gamma"]


def test_render_peer_answer_leads_with_the_target():
    """In a pane full of transient blocks the first token is how you tell
    "beta answered this" from "this is my own agent talking".

    Asserted against the header renderable directly rather than a rendered
    frame: the answer is a Markdown body inside an aside Panel, so a prefix
    check on the whole block would be pinning the panel's border and
    padding instead of the property.
    """
    from rich.console import Console
    from aegis.render import render_peer_answer
    from aegis.tui.themes import INK, aegis_colors
    colors = aegis_colors(INK)
    block = render_peer_answer(
        PeerAnswer(answer="green", target="beta", ok=True), colors)
    assert block.renderable.renderables[0].plain.startswith("@beta ")
    console = Console(width=100, no_color=True)
    with console.capture() as cap:
        console.print(block)
    assert "green" in cap.get()


# ---------- an unknown handle names the alternative ----------------------
#
# refusal()'s own contract is "every refusal names the alternative", and
# `unknown session: peer` was the one branch that didn't. It is also the
# branch people actually hit, because the feature is *called* @peer in
# every doc and conversation about it — so `@peer <question>` reads as the
# command and asks for a session literally named "peer".

from types import SimpleNamespace


def _sess(handle, state="ready"):
    return SimpleNamespace(handle=handle, state=state)


def test_an_unknown_handle_lists_the_live_ones():
    from aegis.peer import refusal
    why = refusal(from_handle="me", target="peer", session=None, ready=False,
                  live=[_sess("me"), _sess("procesos-canvas-ui"),
                        _sess("ainbox-release-airgap")])
    assert "peer" in why
    assert "procesos-canvas-ui" in why
    assert "ainbox-release-airgap" in why


def test_the_asker_is_not_offered_as_an_alternative():
    """You cannot @ yourself — that is what /btw is for — so listing
    yourself as a target would be advice that fails on the next keystroke."""
    from aegis.peer import refusal
    why = refusal(from_handle="me", target="nope", session=None, ready=False,
                  live=[_sess("me"), _sess("other")])
    assert "other" in why
    assert "me" not in why.split("Open now:")[-1]


def test_busy_peers_are_listed_but_marked():
    """Marked rather than hidden, matching the palette completer: a busy
    target is refused, so the constraint is worth seeing now rather than
    hitting it as a second rejection."""
    from aegis.peer import refusal
    why = refusal(from_handle="me", target="nope", session=None, ready=False,
                  live=[_sess("idle-one"), _sess("busy-one", "working")])
    assert "idle-one" in why
    assert "busy-one (busy)" in why


def test_an_unknown_handle_with_no_peers_says_so_rather_than_listing_nothing():
    from aegis.peer import refusal
    why = refusal(from_handle="me", target="nope", session=None, ready=False,
                  live=[_sess("me")])
    assert "no others" in why or "no other" in why
    assert "Open now" not in why


def test_the_other_refusals_are_unchanged():
    from aegis.peer import refusal
    assert "/btw" in refusal(from_handle="me", target="me", session=None,
                             ready=False, live=[_sess("me")])
    assert "mid-turn" in refusal(from_handle="me", target="x",
                                 session=object(), ready=False, live=[])
    assert refusal(from_handle="me", target="x", session=object(),
                   ready=True, live=[]) is None
