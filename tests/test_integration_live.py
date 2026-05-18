import asyncio
import shutil
import pytest
from aegis.config import Agent
from aegis.drivers.claude import ClaudeDriver
from aegis.events import AssistantText, Result

pytestmark = pytest.mark.skipif(
    shutil.which("claude") is None, reason="claude not on PATH"
)


def test_live_claude_say_hi():
    agent = Agent(harness="claude-code", model="sonnet",
                  effort="low", permission="read")
    sess = ClaudeDriver().session(
        agent, ".", "http://127.0.0.1:9/mcp/", "lucid-knuth")

    async def go():
        await sess.start()
        await sess.send("Reply with exactly: HELLO_AEGIS")
        seen_text = seen_result = False
        async for ev in sess.events():
            if isinstance(ev, AssistantText):
                seen_text = True
            if isinstance(ev, Result):
                seen_result = True
        await sess.close()
        return seen_text, seen_result

    seen_text, seen_result = asyncio.run(
        asyncio.wait_for(go(), timeout=120)
    )
    assert seen_text and seen_result
