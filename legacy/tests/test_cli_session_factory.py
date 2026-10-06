from __future__ import annotations

from aegis.cli import _session_factory
from aegis.config import Agent, ClaudeCode
from aegis.drivers.claude import ClaudeSession


def _agent():
    return Agent(provider=ClaudeCode(model="opus"))


def test_factory_builds_a_plain_session_by_default():
    make = _session_factory("/tmp/work")
    sess = make(_agent(), "http://x", "h")
    assert isinstance(sess, ClaudeSession)
    assert "--fork-session" not in sess._argv


def test_factory_builds_a_forked_session_when_given_fork_from():
    """The seam that lets SessionManager.fork reach the driver — without
    it the manager branches its own bookkeeping and spawns a cold agent."""
    make = _session_factory("/tmp/work")
    sess = make(_agent(), "http://x", "h", fork_from="parent-sid")
    assert "--fork-session" in sess._argv
    assert sess._argv[sess._argv.index("--resume") + 1] == "parent-sid"


def test_factory_passes_the_configured_cwd_through():
    make = _session_factory("/tmp/work")
    assert make(_agent(), "http://x", "h")._cwd == "/tmp/work"
