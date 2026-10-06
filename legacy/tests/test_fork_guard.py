from __future__ import annotations

from aegis.core.fork_guard import ForkFacts, facts_for, refuse_reasons


def _facts(**over):
    base = dict(exists=True, session_id="sid-1", supports_fork=True,
                state="ready", driver="claude-code")
    base.update(over)
    return ForkFacts(**base)


def test_no_reasons_when_forkable():
    assert refuse_reasons(_facts(), target="peer") == []


def test_unknown_target():
    assert refuse_reasons(_facts(exists=False), target="ghost") \
        == ["no session 'ghost'"]


def test_refuses_before_first_session_id():
    """Nothing to fork from — the target hasn't produced its first
    SystemInit, so there is no driver-side conversation yet."""
    reasons = refuse_reasons(_facts(session_id=None), target="peer")
    assert len(reasons) == 1
    assert "session id" in reasons[0]


def test_refuses_when_driver_cannot_fork():
    reasons = refuse_reasons(
        _facts(supports_fork=False, driver="gemini"), target="peer")
    assert len(reasons) == 1
    assert "gemini" in reasons[0]


def test_refuses_mid_turn():
    """Measured 2026-07-31: claude flushes each message as it produces
    it, so a live session's tail is an assistant tool_use with no
    tool_result. Forking that burned 42.7s and $1.38 for no answer."""
    reasons = refuse_reasons(_facts(state="working"), target="peer")
    assert len(reasons) == 1
    assert "mid-turn" in reasons[0]


def test_reports_every_reason_at_once():
    """The house pattern from close_guard: an agent that has to call
    again for each new reason learns nothing about how long to wait."""
    reasons = refuse_reasons(
        _facts(session_id=None, supports_fork=False, state="working",
               driver="gemini"),
        target="peer")
    assert len(reasons) == 3


def test_missing_session_takes_precedence_over_everything():
    """A nonexistent target has no meaningful state to report on."""
    assert refuse_reasons(
        _facts(exists=False, session_id=None, supports_fork=False,
               state="working"),
        target="ghost") == ["no session 'ghost'"]


# --- facts_for: one gatherer, two call sites ------------------------------
# SessionManager (serve/MCP) and AegisApp (TUI) build panes by different
# paths but must refuse forks for identical reasons. Sharing the gatherer
# is what keeps the two from drifting apart.

class _FakeAgent:
    def __init__(self, harness="claude-code"):
        self.harness = harness


class _FakeState:
    def __init__(self, value):
        self.value = value


class _FakeSession:
    def __init__(self, *, session_id="sid-1", harness="claude-code",
                 state="ready"):
        self.session_id = session_id
        self.agent = _FakeAgent(harness)
        self.state = _FakeState(state)


def test_facts_for_missing_session_reports_absence():
    f = facts_for(None, capability=lambda h: True)
    assert f.exists is False


def test_facts_for_reads_session_id_state_and_harness():
    f = facts_for(_FakeSession(state="working"), capability=lambda h: True)
    assert f.exists is True
    assert f.session_id == "sid-1"
    assert f.state == "working"
    assert f.driver == "claude-code"


def test_facts_for_asks_the_capability_callback_about_the_harness():
    seen = []

    def cap(harness):
        seen.append(harness)
        return False

    f = facts_for(_FakeSession(harness="gemini"), capability=cap)
    assert seen == ["gemini"]
    assert f.supports_fork is False
