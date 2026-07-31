"""`@handle …` is sugar for `/peer handle …`.

The whole point of routing it as a rewrite rather than a new input path:
the existing dispatcher, the `CommandResult.effect` channel, the palette
and the web seam all carry it unchanged. `wssession.py` calls
`classify_input` on every line, so the web client gets `@` for free.
"""
from __future__ import annotations

from aegis.commands import classify_input, complete


class FakeInfo:
    def __init__(self, handle, slug="claude", state="ready"):
        self.handle, self.agent_slug, self.state = handle, slug, state
        self.active = False
        self.unseen = False


class FakeBridge:
    def __init__(self, *sessions):
        self._sessions = list(sessions)

    def list_sessions(self):
        return self._sessions

    def list_agents(self):
        return []


BRIDGE = FakeBridge(FakeInfo("beta"), FakeInfo("bertha"),
                    FakeInfo("gamma", state="working"))


# ---------- classify_input ----------------------------------------------

def test_at_handle_rewrites_to_the_peer_command():
    assert classify_input("@beta is the build green?") == (
        "command", "/peer beta is the build green?")


def test_at_handle_with_no_question_still_routes():
    # It reaches /peer, which answers with its own usage error — better
    # than silently delivering "@beta" to the agent you're sitting with.
    assert classify_input("@beta") == ("command", "/peer beta")


def test_double_at_escapes_to_a_literal_message():
    assert classify_input("@@beta") == ("message", "@beta")


def test_a_bare_at_is_a_plain_message():
    assert classify_input("@") == ("message", "@")


def test_an_at_followed_by_space_is_a_plain_message():
    # "@ me" is prose, not an address.
    assert classify_input("@ beta hi") == ("message", "@ beta hi")


def test_an_email_mid_line_is_untouched():
    assert classify_input("ping me at a@b.com") == (
        "message", "ping me at a@b.com")


def test_the_slash_family_is_unchanged():
    assert classify_input("//foo") == ("message", "/foo")
    assert classify_input("/btw hi") == ("command", "/btw hi")
    assert classify_input("plain text") == ("message", "plain text")


# ---------- completion ---------------------------------------------------

def test_bare_at_offers_every_live_peer():
    items = complete("@", BRIDGE).items
    assert {c.label for c in items} == {"beta", "bertha", "gamma"}


def test_at_completion_inserts_the_whole_token():
    # `_accept_completion` (pane.py:1337) replaces the entire input when
    # there is no space in it, so the insert has to carry its own `@`.
    items = complete("@bet", BRIDGE).items
    assert items[0].insert.startswith("@")
    assert items[0].insert == "@beta " or items[0].insert == "@bertha "


def test_busy_peers_are_marked_not_hidden():
    detail = {c.label: c.detail for c in complete("@", BRIDGE).items}
    assert "busy" in detail["gamma"]
    assert "busy" not in detail["beta"]


def test_double_at_completes_nothing():
    assert complete("@@bet", BRIDGE).items == ()


def test_completion_stops_once_the_question_begins():
    # Past the handle the next positional is greedy — free text, nothing
    # to offer.
    assert complete("@beta is the ", BRIDGE).items == ()


def test_slash_completion_still_works():
    assert complete("/pe", BRIDGE).items[0].label.startswith("/pe")
