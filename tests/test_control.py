import asyncio
from pathlib import Path

import pytest

from aegis.claude.process import ClaudeProcess, ControlError, build_argv


async def start(fake: str, tmp_path: Path, lines: list[str]) -> ClaudeProcess:
    p = ClaudeProcess(
        build_argv(fake, "opus", "high", "full"),
        tmp_path,
        tmp_path / "err.log",
        lines.append,
        lambda code, tail: None,
    )
    await p.start()
    return p


async def test_initialize_answers_with_commands_and_models_and_is_not_a_line(
    tmp_path, fake_claude
):
    lines: list[str] = []
    p = await start(fake_claude, tmp_path, lines)
    try:
        r = await p.request("initialize")
        assert {"compact", "hello", "sleep"} <= {c["name"] for c in r["commands"]}
        assert any(m["value"] == "haiku" for m in r["models"])
        assert not [x for x in lines if "control_response" in x]
    finally:
        await p.terminate()


async def test_an_error_answer_raises_with_its_message(tmp_path, fake_claude):
    p = await start(fake_claude, tmp_path, [])
    try:
        with pytest.raises(ControlError, match="not found"):
            await p.request("set_model", model="gpt-5")
    finally:
        await p.terminate()


async def test_a_request_to_a_dead_process_fails_fast(tmp_path, fake_claude):
    p = await start(fake_claude, tmp_path, [])
    await p.terminate()
    with pytest.raises(BrokenPipeError):
        await asyncio.wait_for(p.request("initialize"), 2)


async def test_effort_applies_only_where_the_model_lists_it(tmp_path, fake_claude):
    p = await start(fake_claude, tmp_path, [])
    try:
        await p.request("apply_flag_settings", settings={"effortLevel": "max"})
        assert (await p.request("get_settings"))["applied"]["effort"] == "max"
        await p.request("set_model", model="haiku")
        await p.request("apply_flag_settings", settings={"effortLevel": "low"})
        assert (await p.request("get_settings"))["applied"]["effort"] == "max"
    finally:
        await p.terminate()
