import asyncio
from pathlib import Path

from aegis.commands import REGISTRY, CommandContext
from aegis.commands.args import parse
from aegis.commands.prompt_loader import load_prompt_commands


async def _fake_shell(cmd, cwd):
    return f"[ran: {cmd}]"


def _mk(root: Path, name: str, text: str):
    d = root / ".aegis" / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text, encoding="utf-8")


def _clear(names):
    for n in names:
        REGISTRY.pop(n, None)


def test_absent_dir_is_noop(tmp_path):
    assert load_prompt_commands(tmp_path, run_shell=_fake_shell) == []


def test_loads_frontmatter_and_registers(tmp_path):
    _mk(tmp_path, "greet",
        "---\ndescription: say hi\nargument-hint: <name>\n---\nHello $1!")
    names = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    try:
        assert "greet" in names
        cmd = REGISTRY["greet"]
        assert cmd.source == "user"
        assert cmd.summary == "say hi"
        assert cmd.usage == "/greet <name>"
        assert cmd.spec.positionals[0].greedy is True
    finally:
        _clear(names)


def test_run_returns_deliver_effect(tmp_path):
    _mk(tmp_path, "greet", "---\ndescription: hi\n---\nHello $1!")
    names = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    try:
        cmd = REGISTRY["greet"]
        args = parse(cmd.spec, "World")
        res = asyncio.run(cmd.run(CommandContext(bridge=None, handle="h"), args))
        assert res.ok is True
        assert res.effect == {"kind": "deliver", "text": "Hello World!"}
    finally:
        _clear(names)


def test_bad_include_returns_error_result(tmp_path):
    _mk(tmp_path, "bad", "---\ndescription: x\n---\n@missing.md")
    names = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    try:
        cmd = REGISTRY["bad"]
        res = asyncio.run(cmd.run(CommandContext(bridge=None, handle="h"),
                                  parse(cmd.spec, "")))
        assert res.ok is False
        assert res.effect is None
    finally:
        _clear(names)


def test_reload_is_idempotent(tmp_path):
    _mk(tmp_path, "greet", "---\ndescription: hi\n---\nHello")
    a = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    b = load_prompt_commands(tmp_path, run_shell=_fake_shell)   # no raise
    try:
        assert "greet" in a and "greet" in b
    finally:
        _clear(a)
