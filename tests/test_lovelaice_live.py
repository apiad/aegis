import shutil

import pytest

from aegis.config import Agent, Lovelaice
from aegis.drivers.lovelaice import LovelaiceDriver

pytestmark = pytest.mark.skipif(
    shutil.which("lovelaice-acp") is None, reason="lovelaice-acp not on PATH")


@pytest.mark.asyncio
async def test_lovelaice_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("LOVELAICE_FAKE_LLM", "1")
    agent = Agent(provider=Lovelaice(model="fake/model"))
    driver = LovelaiceDriver()
    sess = driver.session(agent, str(tmp_path), "", "handle")
    await sess.start()
    await sess.send("hello")
    kinds = [type(ev).__name__ async for ev in sess.events()]
    await sess.close()
    assert "Result" in kinds  # terminal event from AcpSession.send
