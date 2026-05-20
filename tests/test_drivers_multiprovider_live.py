"""Live smoke tests for the Gemini and OpenCode drivers.

Each test spawns a real CLI subprocess with a trivial prompt, parses
the stream, and asserts we received at least one AssistantText and
a non-error Result. Skips when the relevant CLI isn't on PATH.

These are the bare-minimum proofs that the driver's argv + parser
actually compose against the real binary's output. The TDD-style live
test for cross-provider task passing lives in test_workflow_live.py
once we wire it up explicitly.
"""
from __future__ import annotations

import shutil

import pytest

from aegis import Agent, GeminiCLI, OpenCode
from aegis.drivers.gemini import GeminiDriver
from aegis.drivers.opencode import OpenCodeDriver
from aegis.events import AssistantText, Result


_HAVE_GEMINI = shutil.which("gemini") is not None
_HAVE_OPENCODE = shutil.which("opencode") is not None


pytestmark = pytest.mark.live


@pytest.mark.skipif(not _HAVE_GEMINI, reason="gemini CLI not on PATH")
async def test_gemini_driver_round_trip(tmp_path):
    """Gemini CLI: send a one-word prompt, parse the stream, get a
    success Result back."""
    agent = Agent(provider=GeminiCLI(
        model="gemini-3-flash-preview", permission="full"))
    driver = GeminiDriver()
    sess = driver.session(agent, str(tmp_path), mcp_url="", handle="g1")
    await sess.start()
    await sess.send("Reply with the single word PING and stop.")

    saw_text = False
    saw_result_ok = False
    async for ev in sess.events():
        if isinstance(ev, AssistantText) and "PING" in ev.text.upper():
            saw_text = True
        if isinstance(ev, Result):
            saw_result_ok = not ev.is_error
    await sess.close()

    assert saw_text, "gemini did not emit an AssistantText containing PING"
    assert saw_result_ok, "gemini Result was missing or marked is_error"


@pytest.mark.skipif(not _HAVE_OPENCODE, reason="opencode CLI not on PATH")
async def test_opencode_driver_round_trip(tmp_path):
    """OpenCode CLI: send a one-word prompt, parse the stream, get a
    success Result back. Model defaults to whatever opencode picks
    when no -m is forced — pick a small one explicitly."""
    agent = Agent(provider=OpenCode(
        model="opencode/claude-haiku-4-5", permission="full"))
    driver = OpenCodeDriver()
    sess = driver.session(agent, str(tmp_path), mcp_url="", handle="o1")
    await sess.start()
    await sess.send("Reply with the single word PONG and stop.")

    saw_text = False
    saw_result_ok = False
    async for ev in sess.events():
        if isinstance(ev, AssistantText) and "PONG" in ev.text.upper():
            saw_text = True
        if isinstance(ev, Result):
            saw_result_ok = not ev.is_error
    await sess.close()

    assert saw_text, "opencode did not emit an AssistantText containing PONG"
    assert saw_result_ok, "opencode Result was missing or marked is_error"
