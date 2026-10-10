"""The journal end to end: real sessions on the fake claude, the real app."""

import pytest

from aegis.transcript.entries import fold_records
from aegis.transcript.store import read_store

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


def _records(world, log_id):
    return read_store(world.app.sessions.store_path(log_id))[0]


async def test_the_spawn_record_names_the_handle_and_a_rename_is_recorded(world):
    a = await world.spawn()
    first = a.handle
    world.app.sessions.rename(a.log_id, "new-name", None)
    recs = _records(world, a.log_id)
    assert [r["handle"] for r in recs if r.get("kind") == "spawn"] == [first]
    names = [r for r in recs if r.get("kind") == "name"]
    assert [(r["handle"], r["title"]) for r in names] == [("new-name", a.title)]
    world.app.sessions.rename(a.log_id, None, "just a title")
    world.app.sessions.rename(a.log_id, "new-name", None)
    recs = _records(world, a.log_id)
    assert len([r for r in recs if r.get("kind") == "name"]) == 1


async def test_a_rename_leaves_last_activity_alone(world):
    a = await world.spawn()
    before = a.last_activity
    world.app.sessions.rename(a.log_id, "new-name", "a title")
    assert a.last_activity == before


async def test_closing_records_a_close_that_the_fold_draws(world):
    a = await world.spawn()
    log_id = a.log_id
    await world.app.sessions.close(log_id)
    recs = _records(world, log_id)
    assert recs[-1]["kind"] == "close"
    assert fold_records(recs).entries()[-1]["summary"] == "closed"
