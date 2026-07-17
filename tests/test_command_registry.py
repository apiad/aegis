import pytest
from aegis.commands import (
    REGISTRY, SlashCommand, register, CommandCollision, classify_input,
)


def test_classify_single_slash_is_command():
    assert classify_input("/sessions") == ("command", "/sessions")


def test_classify_double_slash_is_literal_message():
    assert classify_input("//not a command") == ("message", "/not a command")


def test_classify_plain_is_message():
    assert classify_input("hello there") == ("message", "hello there")


async def _noop(ctx, args):  # signature is irrelevant to this task's checks
    return None


def _restore(snapshot):
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def test_builtin_registers_and_carries_source():
    snap = dict(REGISTRY)
    try:
        register(SlashCommand("t_reg_a", "s", "/t_reg_a", _noop))
        assert REGISTRY["t_reg_a"].source == "builtin"
    finally:
        _restore(snap)


def test_user_cannot_override_builtin():
    snap = dict(REGISTRY)
    try:
        register(SlashCommand("t_reg_b", "s", "/t_reg_b", _noop))  # builtin
        with pytest.raises(CommandCollision):
            register(SlashCommand("t_reg_b", "s", "/t_reg_b", _noop,
                                  source="user"))
    finally:
        _restore(snap)


def test_user_fresh_name_registers():
    snap = dict(REGISTRY)
    try:
        register(SlashCommand("t_reg_c", "s", "/t_reg_c", _noop,
                              source="user"))
        assert REGISTRY["t_reg_c"].source == "user"
    finally:
        _restore(snap)
