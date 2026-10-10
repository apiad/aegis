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


def test_the_catalog_reads_sources_cuts_docs_and_drops_disabled_models():
    from aegis.claude import control

    cat = control.from_initialize(
        {
            "commands": [
                {
                    "name": "compact",
                    "description": "Clear history but keep a summary. Extra.",
                    "argumentHint": "",
                    "builtin": True,
                },
                {
                    "name": "draft",
                    "description": "Generate a draft. (project)",
                    "argumentHint": "<outline>",
                },
                {"name": "ingest", "description": "x" * 300},
                {"name": "sync", "description": "Sync it. (claude.ai sync)"},
            ],
            "models": [
                {
                    "value": "sonnet",
                    "resolvedModel": "claude-sonnet-5",
                    "displayName": "Sonnet 5",
                    "description": "d",
                    "supportedEffortLevels": ["low"],
                },
                {
                    "value": "old",
                    "resolvedModel": "x",
                    "displayName": "Old",
                    "description": "d",
                    "disabled": True,
                },
            ],
        }
    )
    by = {c["name"]: c for c in cat.wire_commands(shadowed=())}
    assert by["compact"] == {
        "name": "compact",
        "hint": "",
        "doc": "Clear history but keep a summary.",
        "source": "claude",
    }
    assert (by["draft"]["source"], by["draft"]["doc"], by["draft"]["hint"]) == (
        "project",
        "Generate a draft.",
        "<outline>",
    )
    assert by["ingest"]["source"] == "skill" and len(by["ingest"]["doc"]) == 140
    assert by["sync"]["source"] == "claude.ai sync"
    assert cat.model("claude-sonnet-5").value == "sonnet" and cat.model("old") is None
    assert [m["value"] for m in cat.wire_models()] == ["sonnet"]
    assert cat.wire_models()[0]["resolved"] == "claude-sonnet-5"
    assert "compact" not in {
        c["name"] for c in cat.wire_commands(shadowed=("compact",))
    }


async def test_the_setters_switch_a_live_fake_and_effort_is_read_back(
    tmp_path, fake_claude
):
    from aegis.claude import control

    p = await start(fake_claude, tmp_path, [])
    try:
        cat = await control.catalog(p)
        assert cat.has("hello") and not cat.has("bogus")
        await control.set_model(p, "sonnet")
        await control.set_effort(p, "xhigh")
        await control.set_permission(p, "read")
        await control.set_model(p, "haiku")
        with pytest.raises(ControlError, match="did not apply effort low"):
            await control.set_effort(p, "low")
    finally:
        await p.terminate()


async def test_a_probe_answers_and_leaves_no_process(tmp_path, fake_claude):
    from aegis.claude import control

    cat = await control.probe(
        fake_claude, "opus", "high", "full", tmp_path, tmp_path / "probe.log"
    )
    assert cat.has("compact")


def test_each_read_dir_is_an_added_dir():
    argv = build_argv("claude", "opus", "high", "read", add_dirs=(Path("/s/inbox/l"),))
    assert argv[argv.index("--add-dir") + 1] == "/s/inbox/l"
    assert "--add-dir" not in build_argv("claude", "opus", "high", "read")


def test_every_permission_lets_aegis_tools_through_a_hook():
    """Plan mode refuses an MCP call an allow rule names; a hook's allow
    passes it, and aegis's registry decides what a read session may call (#305)."""
    import json

    for permission in ("read", "write", "auto", "full"):
        argv = build_argv("claude", "opus", "high", permission)
        (rule,) = json.loads(argv[argv.index("--settings") + 1])["hooks"]["PreToolUse"]
        assert rule["matcher"] == "mcp__aegis__.*"
        assert '"permissionDecision": "allow"' in rule["hooks"][0]["command"]
