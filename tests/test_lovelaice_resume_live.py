"""VS5 proof: load_session resume across subprocess restarts restores context.

Session 1 tells the agent a codeword and closes (subprocess dies); a fresh
resumed session recalls it. Gated on lovelaice-acp + an OpenRouter key.
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from aegis.config import Agent, Lovelaice
from aegis.drivers.lovelaice import LovelaiceDriver

TOKEN = "/home/apiad/Workspace/.claude/openrouter.token"

pytestmark = [
    pytest.mark.skipif(shutil.which("lovelaice-acp") is None,
                       reason="lovelaice-acp not on PATH"),
    pytest.mark.skipif(not Path(TOKEN).is_file(), reason="no OpenRouter token"),
]


def _agent():
    return Agent(provider=Lovelaice(
        model="anthropic/claude-haiku-4-5",
        base_url="https://openrouter.ai/api/v1", api_key_file=TOKEN))


@pytest.mark.asyncio
async def test_resume_restores_context():
    tmp = tempfile.mkdtemp()
    drv = LovelaiceDriver()

    s1 = drv.session(_agent(), tmp, "", "r1")
    await s1.start()
    await s1.send("Remember the codeword is BANANA. Acknowledge briefly.")
    async for _ in s1.events():
        pass
    sid = s1.session_id
    await s1.close()
    assert sid, "session_id must be exposed for resume"

    s2 = drv.resume(_agent(), tmp, "", "r2", session_id=sid)
    await s2.start()
    await s2.send("What is the codeword I told you? Reply with just the word.")
    text = ""
    async for ev in s2.events():
        if type(ev).__name__ == "AssistantText":
            text += getattr(ev, "text", "")
    await s2.close()
    assert "BANANA" in text.upper(), f"resumed agent did not recall: {text!r}"
