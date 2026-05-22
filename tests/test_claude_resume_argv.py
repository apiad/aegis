from __future__ import annotations

from aegis.config import Agent, ClaudeCode
from aegis.drivers.claude import ClaudeDriver, ClaudeSession
from aegis.events import AssistantText, SystemInit


def _agent():
    return Agent(provider=ClaudeCode(model="opus"))


def test_resume_argv_has_resume_session_id_flag():
    d = ClaudeDriver()
    sess = d.resume(_agent(), cwd="/tmp", mcp_url="http://x",
                    handle="h", session_id="abc-123")
    assert isinstance(sess, ClaudeSession)
    assert "--resume" in sess._argv
    idx = sess._argv.index("--resume")
    assert sess._argv[idx + 1] == "abc-123"
    # all other claude flags preserved
    for flag in ("--input-format", "--output-format", "--model",
                 "--permission-mode", "--mcp-config"):
        assert flag in sess._argv


def test_claude_session_initially_has_no_session_id():
    d = ClaudeDriver()
    sess = d.session(_agent(), cwd="/tmp", mcp_url="http://x", handle="h")
    assert sess.session_id is None


def test_claude_session_latches_session_id_on_systeminit():
    d = ClaudeDriver()
    sess = d.session(_agent(), cwd="/tmp", mcp_url="http://x", handle="h")
    sess._latch_session_id(SystemInit(session_id="abc-123"))
    assert sess.session_id == "abc-123"
    # Second SystemInit does not overwrite the latched value
    sess._latch_session_id(SystemInit(session_id="other-id"))
    assert sess.session_id == "abc-123"
    # SystemInit with None does not clear the latched value
    sess._latch_session_id(SystemInit(session_id=None))
    assert sess.session_id == "abc-123"
    # Non-SystemInit events are ignored
    sess._latch_session_id(AssistantText(text="x", usage=None))
    assert sess.session_id == "abc-123"
