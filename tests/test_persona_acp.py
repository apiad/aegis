import pytest

from aegis.config import Agent, GeminiCLI
from aegis.drivers.acp import AcpSession


class _StubConn:
    def __init__(self):
        self.prompts = []

    async def prompt(self, *, session_id, prompt):
        self.prompts.append(prompt)

        class R:  # minimal PromptResponse-ish
            stop_reason = "end_turn"
            usage = None
            field_meta = None
        return R()


@pytest.mark.asyncio
async def test_first_send_prepends_persona():
    agent = Agent(provider=GeminiCLI(model="gemini-2.5-pro"))
    sess = AcpSession(agent, cwd=".", mcp_url="", handle="h",
                      persona="PERSONA-X")
    sess._conn = _StubConn()
    sess._session_id = "sid"
    await sess.send("hello")
    blocks = sess._conn.prompts[0]
    assert blocks[0]["text"] == "PERSONA-X"
    assert blocks[-1]["text"] == "hello"
    # second turn does NOT re-inject
    await sess.send("again")
    assert all(b["text"] != "PERSONA-X" for b in sess._conn.prompts[1])


@pytest.mark.asyncio
async def test_no_persona_single_block():
    agent = Agent(provider=GeminiCLI(model="gemini-2.5-pro"))
    sess = AcpSession(agent, cwd=".", mcp_url="", handle="h")
    sess._conn = _StubConn()
    sess._session_id = "sid"
    await sess.send("hello")
    assert sess._conn.prompts[0] == [{"type": "text", "text": "hello"}]
