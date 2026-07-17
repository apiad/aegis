from aegis.commands import complete
from aegis.commands.args import Arg, ArgSpec
from aegis.commands import SlashCommand, register, REGISTRY, CommandResult


class FakeBridge:
    def list_agents(self):
        return ["default", "opus"]


async def _noop(ctx, args):
    return CommandResult(True, "ok")


def _register_probe():
    register(SlashCommand(
        "probe2d", "probe", "/probe2d [sub] [agent]", _noop,
        spec=ArgSpec(positionals=(
            Arg("sub", required=False, completer=("alpha", "beta")),
            Arg("agent", required=False,
                completer=lambda b: b.list_agents()))),
        source="builtin"))


def test_verb_in_progress_lists_commands():
    res = complete("/sess", FakeBridge())
    assert any(c.label == "/sessions" for c in res.items)
    assert all(c.insert.endswith(" ") for c in res.items)   # ready for args


def test_bare_slash_lists_all():
    res = complete("/", FakeBridge())
    assert len(res.items) >= 5


def test_not_a_command_is_empty():
    assert complete("hello", FakeBridge()).items == ()


def test_static_tuple_completer():
    _register_probe()
    try:
        res = complete("/probe2d al", FakeBridge())
        assert [c.label for c in res.items] == ["alpha"]
    finally:
        REGISTRY.pop("probe2d", None)


def test_callable_completer_uses_bridge():
    _register_probe()
    try:
        res = complete("/probe2d alpha op", FakeBridge())
        assert [c.label for c in res.items] == ["opus"]
    finally:
        REGISTRY.pop("probe2d", None)


def test_hint_reflects_positionals():
    res = complete("/spawn ", FakeBridge())
    assert "agent" in res.hint


def test_flag_completion():
    res = complete("/agents add r claude-code sonnet --eff", FakeBridge())
    assert any(c.label == "--effort" for c in res.items)


def test_greedy_positional_yields_no_items():
    res = complete("/spawn opus write a ", FakeBridge())
    assert res.items == ()


def test_throwing_completer_is_swallowed():
    def _boom(b):
        raise RuntimeError("nope")
    register(SlashCommand(
        "probe2dboom", "x", "/probe2dboom [a]", _noop,
        spec=ArgSpec(positionals=(Arg("a", required=False, completer=_boom),)),
        source="builtin"))
    try:
        res = complete("/probe2dboom x", FakeBridge())
        assert res.items == ()
    finally:
        REGISTRY.pop("probe2dboom", None)
