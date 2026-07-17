import pytest
from aegis.commands import (
    REGISTRY, SlashCommand, register, CommandCollision, classify_input,
    CommandResult,
)


def test_command_result_effect_defaults_none():
    assert CommandResult(True, "t").effect is None


def test_command_result_carries_effect():
    r = CommandResult(True, "t", effect={"kind": "clear"})
    assert r.effect == {"kind": "clear"}


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


def _cmd(name, source):
    return SlashCommand(name, "s", f"/{name}", _noop, source=source)


def test_user_replaces_plugin_regardless_of_order():
    snap = dict(REGISTRY)
    try:
        register(_cmd("t_reg_d", "plugin"))
        register(_cmd("t_reg_d", "user"))            # higher priority replaces
        assert REGISTRY["t_reg_d"].source == "user"
    finally:
        _restore(snap)


def test_plugin_cannot_shadow_user():
    snap = dict(REGISTRY)
    try:
        register(_cmd("t_reg_e", "user"))
        with pytest.raises(CommandCollision):
            register(_cmd("t_reg_e", "plugin"))
        assert REGISTRY["t_reg_e"].source == "user"
    finally:
        _restore(snap)


def test_same_source_second_raises():
    snap = dict(REGISTRY)
    try:
        register(_cmd("t_reg_f", "user"))
        with pytest.raises(CommandCollision):
            register(_cmd("t_reg_f", "user"))
    finally:
        _restore(snap)


def test_same_object_reregistration_is_idempotent():
    snap = dict(REGISTRY)
    try:
        c = _cmd("t_reg_g", "user")
        register(c)
        register(c)                                  # same object → no raise
        assert REGISTRY["t_reg_g"] is c
    finally:
        _restore(snap)
