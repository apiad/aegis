"""The `deferred` primitive: a command that must not be awaited inside a
frontend's input handler.

`/btw` takes 12-17s and `@peer` up to `PEER_ASK_TIMEOUT_S = 300`. Awaiting
either inside a Textual message handler holds that pane's message pump for
the duration — no working indicator, no tool spinners, no input. The
property is declared on the command rather than checked by verb because
two commands landed hours apart hit the same defect for the same reason.
"""
from __future__ import annotations

import pytest

from aegis.commands import (
    REGISTRY, SlashCommand, register, resolve_deferred)
from aegis.commands.args import Arg, ArgSpec


@pytest.fixture
def slow_command():
    """A registered deferred command, removed again afterwards."""
    async def _run(ctx, args):
        raise AssertionError("resolve_deferred must not run the handler")

    cmd = SlashCommand(
        "slowly", "a slow command", "/slowly <handle> <question>", _run,
        spec=ArgSpec(positionals=(Arg("handle", required=False),
                                  Arg("question", required=False,
                                      greedy=True))),
        deferred=True,
        cancel_note="stopped waiting — {handle}'s turn is still running",
    )
    register(cmd)
    yield cmd
    REGISTRY.pop("slowly", None)


def test_commands_are_not_deferred_by_default():
    """Opt-in: every command that existed before this change keeps being
    awaited inline, so nothing else has to know the flag exists."""
    assert REGISTRY["help"].deferred is False
    assert REGISTRY["help"].cancel_note == "cancelled"


def test_resolve_deferred_returns_the_command_and_its_parsed_args(
        slow_command):
    resolved = resolve_deferred("/slowly beta is the build green?")
    assert resolved is not None
    cmd, args = resolved
    assert cmd is slow_command
    assert args["handle"] == "beta"
    assert args["question"] == "is the build green?"


def test_resolve_deferred_does_not_run_the_handler(slow_command):
    """It is a lookup, not a dispatch — the fixture's handler raises if
    called. The frontend decides *how* to run the command before it runs."""
    assert resolve_deferred("/slowly beta hi") is not None


def test_resolve_deferred_is_none_for_an_ordinary_command():
    assert resolve_deferred("/help") is None


def test_resolve_deferred_is_none_for_an_unknown_verb():
    assert resolve_deferred("/nosuchverb x") is None


def test_bad_args_fall_through_to_the_inline_path(slow_command):
    """A typo should get dispatch()'s usage error immediately, not a
    spinner. Returning None sends it down the normal await path."""
    assert resolve_deferred("/slowly --nosuchflag") is None


def test_cancel_note_resolves_against_the_parsed_args(slow_command):
    """'cancelled' is a lie for @peer: by the time you press ESC the peer
    has taken the turn and finishes into its own transcript whether or not
    anyone is listening. So the note is per-command and interpolated."""
    cmd, args = resolve_deferred("/slowly beta is the build green?")
    assert cmd.resolved_cancel_note(args) == (
        "stopped waiting — beta's turn is still running")


def test_the_default_cancel_note_needs_no_args():
    """/btw's cancel really is clean — it never touched a harness session,
    so nothing happened anywhere and 'cancelled' is the whole truth."""
    assert REGISTRY["help"].resolved_cancel_note() == "cancelled"


def test_a_template_naming_an_unknown_key_falls_back_intact():
    """The cancel line is the last thing rendered for a command the
    operator already walked away from. It must not be able to raise —
    a broken template should cost you the interpolation, not the note.
    """
    async def _run(ctx, args):
        raise AssertionError("not run")

    cmd = SlashCommand("typo", "s", "/typo", _run, deferred=True,
                       cancel_note="stopped waiting — {nosuchkey} is busy")
    register(cmd)
    try:
        _, args = resolve_deferred("/typo")
        assert cmd.resolved_cancel_note(args) == (
            "stopped waiting — {nosuchkey} is busy")
    finally:
        REGISTRY.pop("typo", None)


# ---------- the effect chain, callable from two places -------------------

class _FakePane:
    """The three `ConversationPane` methods `_apply_command_result` touches,
    recorded rather than mounted. Keeps the chain testable without a running
    Textual app."""

    def __init__(self):
        from aegis.tui.themes import INK, aegis_colors
        self.blocks: list[tuple[object, str, object]] = []
        self.effects: list[dict] = []
        self._palette = aegis_colors(INK)
        self.flushed = 0

    def _flush_streaming(self):
        self.flushed += 1

    def _put_block(self, renderable, payload, *, at_idx=None):
        self.blocks.append((renderable, payload, at_idx))

    def _apply_command_effect(self, effect):
        self.effects.append(effect)


def _apply(pane, result, width=80, at_idx=None):
    from aegis.tui.pane import ConversationPane
    return ConversationPane._apply_command_result(
        pane, result, width, at_idx=at_idx)


def _side_note_result(answer="core/manager.py"):
    from dataclasses import asdict
    from aegis.btw import SideNote
    from aegis.commands import CommandResult
    note = SideNote(answer=answer, ok=True, model="haiku")
    return CommandResult(True, note.answer, "",
                         effect={"kind": "side_note", "note": asdict(note)})


def test_a_side_note_effect_mounts_a_side_note_block():
    pane = _FakePane()
    out = _apply(pane, _side_note_result())
    assert out is None
    assert len(pane.blocks) == 1
    assert "core/manager.py" in pane.blocks[0][1]


def test_a_peer_answer_effect_mounts_a_peer_block():
    """Has to work on both the inline and deferred paths — @peer landed
    deferred=False and flipped to True, and an effect branch that only
    exists on one path breaks it in one of the two states."""
    from dataclasses import asdict
    from aegis.commands import CommandResult
    from aegis.peer import PeerAnswer
    ans = PeerAnswer(answer="green", target="beta", ok=True)
    pane = _FakePane()
    out = _apply(pane, CommandResult(True, ans.answer, "",
                                     effect={"kind": "peer_answer",
                                             "answer": asdict(ans)}))
    assert out is None
    assert "green" in pane.blocks[0][1]


def test_a_deliver_effect_returns_the_text_and_mounts_nothing():
    from aegis.commands import CommandResult
    pane = _FakePane()
    out = _apply(pane, CommandResult(True, "", "",
                                     effect={"kind": "deliver",
                                             "text": "hello"}))
    assert out == "hello"
    assert pane.blocks == []


def test_an_ordinary_result_mounts_a_block_and_applies_its_effect():
    from aegis.commands import CommandResult
    pane = _FakePane()
    out = _apply(pane, CommandResult(True, "switched", "",
                                     effect={"kind": "theme",
                                             "name": "ink"}))
    assert out is None
    assert len(pane.blocks) == 1
    assert pane.effects == [{"kind": "theme", "name": "ink"}]


def test_at_idx_is_forwarded_so_a_result_can_replace_a_placeholder():
    """The deferred path's whole requirement: the answer lands in the
    block the command mounted, not at the tail."""
    pane = _FakePane()
    _apply(pane, _side_note_result(), at_idx=7)
    assert pane.blocks[0][2] == 7


def test_mounting_at_the_tail_is_still_the_default():
    pane = _FakePane()
    _apply(pane, _side_note_result())
    assert pane.blocks[0][2] is None
