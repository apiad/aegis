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
