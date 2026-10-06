from __future__ import annotations

import pytest

from aegis.config import Agent, ClaudeCode
from aegis.drivers.base import HarnessDriver
from aegis.drivers.claude import ClaudeDriver, ClaudeSession
from aegis.drivers.gemini import GeminiDriver
from aegis.drivers.opencode import OpenCodeDriver


def _agent():
    return Agent(provider=ClaudeCode(model="opus"))


def test_fork_argv_has_fork_session_and_resume_flags():
    """A fork is --resume plus --fork-session: load that conversation,
    then branch instead of continuing it. Without --fork-session this is
    a plain resume and the two sessions would fight over one id."""
    d = ClaudeDriver()
    sess = d.fork(_agent(), cwd="/tmp", mcp_url="http://x",
                  handle="h", session_id="abc-123")
    assert isinstance(sess, ClaudeSession)
    assert "--fork-session" in sess._argv
    idx = sess._argv.index("--resume")
    assert sess._argv[idx + 1] == "abc-123"


def test_fork_argv_preserves_every_other_claude_flag():
    d = ClaudeDriver()
    sess = d.fork(_agent(), cwd="/tmp", mcp_url="http://x",
                  handle="h", session_id="abc-123")
    for flag in ("--input-format", "--output-format", "--model",
                 "--permission-mode", "--mcp-config",
                 "--replay-user-messages"):
        assert flag in sess._argv


def test_fork_argv_keeps_claude_p_prefix_first():
    """The flags are inserted after the `claude -p` prefix, not before —
    argv[0] must stay the executable."""
    d = ClaudeDriver()
    sess = d.fork(_agent(), cwd="/tmp", mcp_url="http://x",
                  handle="h", session_id="abc-123")
    assert sess._argv[:2] == ["claude", "-p"]


def test_forked_session_has_no_session_id_until_it_reports_one():
    """A fork gets a NEW driver-side session id, so it must not inherit
    the parent's. Latching the parent's id here would make the two
    conversations share a log and is the bug that buried 160 of them."""
    d = ClaudeDriver()
    sess = d.fork(_agent(), cwd="/tmp", mcp_url="http://x",
                  handle="h", session_id="abc-123")
    assert sess.session_id is None


def test_claude_supports_fork():
    assert ClaudeDriver().supports_fork is True


def test_acp_drivers_do_not_support_fork():
    """ACP v1 loadSession has no fork parameter — the capability map
    stays honest rather than advertising something that would silently
    continue the parent instead of branching."""
    assert GeminiDriver().supports_fork is False
    assert OpenCodeDriver().supports_fork is False


def test_base_driver_fork_raises_naming_the_driver():
    """The default-raises pattern from resume: a refusal that doesn't say
    which driver refused is the failure mode."""

    class Bare(HarnessDriver):
        def build_argv(self, agent, cwd, mcp_url, handle):
            return []

        def session(self, agent, cwd, mcp_url, handle):
            raise AssertionError("not called")

    with pytest.raises(NotImplementedError, match="Bare"):
        Bare().fork(_agent(), cwd="/tmp", mcp_url="http://x",
                    handle="h", session_id="abc-123")
