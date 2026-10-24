# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""The optional switch ``knowledge: native`` (setup.py, knowledge_ops.py): a
person's sessions start without the harness's own skills and memory. Read
from the process the fake claude runs as: its argv and its environment."""

from __future__ import annotations

import time
from pathlib import Path

from aegis.codex.config import argv as codex_argv
from aegis.opencode.config import child_config

from .conftest import until
from .knowledge_samples import skill
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import mcp, turn, world  # noqa: F401  (fixture)
from .test_commands import app, send  # noqa: F401
from .test_commands import spawn as spawn_here
from .test_member_view import homes, spawn  # noqa: F401
from .test_member_view import world as members  # noqa: F401

VAR = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"


def _proc(s, what: str) -> list[str]:
    """A /proc file of the session's process, split on NULs. A live process
    can read as empty for an instant (the kernel reads it from the
    process's memory), so an empty read is tried again."""
    for _ in range(50):
        raw = Path(f"/proc/{s.pid}/{what}").read_bytes()
        if raw:
            return raw.decode().split("\0")[:-1]
        time.sleep(0.01)
    return []


def proc_argv(s) -> list[str]:
    return _proc(s, "cmdline")


def proc_env(s) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in _proc(s, "environ") if "=" in kv)


async def started(*sessions) -> None:
    """Until each process is the harness: the launcher execs it after the
    pid is known."""

    def ready(s) -> bool:
        try:
            return s.pid is not None and "--input-format" in proc_argv(s)
        except OSError:
            return False

    await until(lambda: all(ready(s) for s in sessions), what="the processes")


def denied(s) -> list[str]:
    argv = proc_argv(s)
    if "--disallowedTools" not in argv:
        return []
    return argv[argv.index("--disallowedTools") + 1].split(",")


async def setting(app, caller=None, **kw) -> bool:
    args = (kw, caller) if caller else (kw,)
    return (await app.registry.call("knowledge.setting", *args))["native"]


async def test_native_off_keeps_the_skill_tool(world):
    s = await world.spawn()
    await started(s)
    assert await setting(world.app) is False
    assert "Monitor" in denied(s)  # aegis's own replacements are on
    assert "Skill" not in denied(s)
    assert VAR not in proc_env(s)


async def test_native_on_turns_auto_memory_off(world):
    assert await setting(world.app, native=True) is True
    s = await world.spawn()
    await started(s)
    assert "Skill" in denied(s) and "Monitor" in denied(s)
    assert proc_env(s)[VAR] == "1"


async def test_native_on_disallows_skill_for_that_owner_only(members):
    app = members.app
    assert await setting(app, BEN, native=True) is True
    assert await setting(app) is False  # the admin's own is untouched
    ben = app.sessions.sessions[await spawn(members, BEN)]
    mine = await members.spawn()
    await started(ben, mine)
    assert "Skill" in denied(ben) and proc_env(ben)[VAR] == "1"
    assert "Skill" not in denied(mine) and VAR not in proc_env(mine)


async def test_the_switch_is_undone_and_reaches_the_next_start(world):
    app = world.app
    await setting(app, native=True)
    s = await world.spawn()
    await started(s)
    assert "Skill" in denied(s)
    assert await setting(app, native=False) is False
    assert "Skill" in denied(s)  # a running process keeps how it started
    await s.stop()
    await s.ensure_running()
    await started(s)
    assert "Skill" not in denied(s) and VAR not in proc_env(s)
    assert app.setup_records.get(app.user)["knowledge"] == "side-by-side"


async def test_setup_record_turns_it_on_and_keeps_it_on_a_later_run(world):
    app = world.app
    s = await world.spawn()
    run = dict(
        applied=["knowledge:native"], declined=[], sources_until="2026-10-24T00:00:00Z"
    )
    said = await turn(s, mcp("setup_record", knowledge="native", **run), by_person=True)
    assert said.startswith("mcp ok"), said
    assert app.knowledge_native(app.user)
    # A later run that does not name it leaves the switch as it was.
    said = await turn(s, mcp("setup_record", **run))
    assert said.startswith("mcp ok"), said
    assert app.knowledge_native(app.user)


async def test_setup_record_switches_only_in_a_person_turn(world):
    """A queue task, a handoff or a wake cannot flip the person's switch:
    Settings stays their direct path."""
    app = world.app
    s = await world.spawn()
    run = dict(applied=[], declined=[], sources_until="2026-10-24T00:00:00Z")
    said = await turn(s, mcp("setup_record", knowledge="native", **run))
    assert said.startswith("mcp error: not_person_turn"), said
    assert "knowledge" not in (app.setup_records.get(app.user) or {})
    assert not app.knowledge_native(app.user)
    await setting(app, native=True)
    said = await turn(s, mcp("setup_record", knowledge="side-by-side", **run))
    assert said.startswith("mcp error: not_person_turn"), said
    assert app.knowledge_native(app.user)
    said = await turn(
        s, mcp("setup_record", knowledge="side-by-side", **run), by_person=True
    )
    assert said.startswith("mcp ok"), said
    assert not app.knowledge_native(app.user)


async def test_with_the_setting_a_store_skill_takes_a_harness_command_name(app):
    """Slice 2's rule, now on the real setting: the fake claude lists /busy."""
    await app.knowledge.store.save(app.user, skill("busy", "The store's busy"), "t")
    lid = await spawn_here(app)
    rows = (await app.registry.call("commands.list", {"log_id": lid}))["commands"]
    assert "skill:busy" in {c["name"] for c in rows}
    await setting(app, native=True)
    rows = (await app.registry.call("commands.list", {"log_id": lid}))["commands"]
    assert [c["source"] for c in rows if c["name"] == "busy"] == ["aegis skill"]


def test_codex_native_turns_its_skills_and_memories_off():
    off = codex_argv("codex", "http://x/mcp", native=True)
    on = codex_argv("codex", "http://x/mcp")
    pairs = list(zip(off, off[1:]))
    assert ("--disable", "memories") in pairs
    for kv in (
        "skills.include_instructions=false",
        "skills.bundled.enabled=false",
        "memories.use_memories=false",
        "memories.generate_memories=false",
    ):
        assert ("-c", kv) in pairs
    assert "memories" not in on and not any(a.startswith("skills.") for a in on)
    assert off[:2] == ["codex", "app-server"]


def test_opencode_native_denies_the_skill_action():
    rules = child_config(None, "full", (), native=True)["permissions"]
    assert {"action": "skill", "resource": "*", "effect": "deny"} in rules
    # The deny comes after the "*" allow: OpenCode applies the last match.
    actions = [r["action"] for r in rules]
    assert actions.index("*") < actions.index("skill")
    plain = child_config(None, "full")["permissions"]
    assert all(r["action"] != "skill" for r in plain)
