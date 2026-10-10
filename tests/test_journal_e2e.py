"""The journal end to end: real sessions on the fake claude, the real app."""

import pytest

from .test_agents import CONFIG, World, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_the_fake_runs_a_command_writes_and_edits(world):
    a = await world.spawn()
    f = world.root / "out.txt"
    await turn(a, f"/sh echo hi > {f}")
    assert f.read_text() == "hi\n"
    await turn(a, f"/write {f} => one two")
    await turn(a, f"/edit {f} one => three")
    assert f.read_text() == "three two"
    tools = [e for e in a.entries() if e["kind"] == "tool"]
    assert [e["title"] for e in tools][-3:] == ["Bash", "Write", "Edit"]
    assert all(e["status"] == "ok" for e in tools[-3:])
