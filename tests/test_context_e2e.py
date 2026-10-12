"""Context size, cache counters and the handoff, end to end on the fake claude."""

import pytest

from .test_agents import CONFIG, World, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_ctx_sets_the_context_size(world):
    a = await world.spawn()
    await turn(a, "/ctx 350000")
    assert a.context_tokens == 350000
