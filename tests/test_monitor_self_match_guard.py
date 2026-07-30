"""`pgrep -f` / `pkill -f` are rejected in monitor conditions.

The condition runs in a shell whose own command line contains the pattern,
so the pattern matches itself: `pgrep -f 'pytest …'` is true even when
nothing is running, and the `! pgrep -f …` spelling of "it finished" is
therefore false forever. The monitor sits there until it times out, and
the agent reports a hang that never happened.

Deterministic guard rather than documentation, because the failure is
silent and reads as "the process is still going".
"""
import pytest

from aegis.monitor.schema import condition_error


class TestTheGuardItself:
    @pytest.mark.parametrize("cond", [
        "! pgrep -f 'pytest -q' >/dev/null",
        "pgrep -f mybuild",
        "pgrep -af node",
        "pgrep --full 'uv run'",
        "pkill -f staleserver",
        "test -f out && ! pgrep -f 'make build'",
        "PGREP -F FOO",
        "x=$(pgrep -f mybuild)",
        "while ! pgrep -f mybuild; do sleep 1; done",
        "if pgrep -f mybuild; then false; fi",
    ])
    def test_rejects_full_cmdline_matching(self, cond):
        err = condition_error(cond)
        assert err is not None, f"should have been rejected: {cond!r}"
        assert "pgrep -f" in err
        # The error has to say what to do instead, not just say no.
        assert "marker" in err.lower() or "kill -0" in err

    @pytest.mark.parametrize("cond", [
        "grep -q DONE build.log",
        "test -f build/out",
        "! kill -0 12345 2>/dev/null",
        "pgrep -x mydaemon",              # -x matches the name, not the cmdline
        "pgrep mydaemon",
        "grep -q 'pgrep -f' run.log",     # the name inside someone else's arg
        "test -f /tmp/pgrep-f-notes.txt",
        "curl -sf localhost:8080/health",
        "",
    ])
    def test_allows_everything_else(self, cond):
        assert condition_error(cond) is None, f"wrongly rejected: {cond!r}"

    def test_none_is_fine(self):
        assert condition_error(None) is None


class TestStartMonitorRefuses:
    def _mm(self):
        from tests.test_monitor_mcp import _mm
        return _mm()

    @pytest.mark.parametrize("field", ["done", "fail", "progress"])
    def test_every_condition_field_is_checked(self, field):
        mm = self._mm()
        kwargs = {"from_handle": "p", "description": "build",
                  "done": "test -f out"}
        kwargs[field] = "! pgrep -f 'make build'"
        with pytest.raises(ValueError, match="pgrep -f"):
            mm.start_monitor(autorun=False, **kwargs)

    def test_a_clean_monitor_still_starts(self):
        mm = self._mm()
        mid = mm.start_monitor(from_handle="p", description="build",
                               done="grep -q DONE out.log",
                               progress="echo 50", autorun=False)
        assert mid


@pytest.mark.asyncio
async def test_mcp_tool_returns_the_error_not_an_exception():
    """The agent must get a readable {error: ...}, the same shape the cwd
    check returns — not a traceback."""
    from aegis.mcp.server import build_server
    from tests.test_monitor_mcp import _Bridge, _call, _mm

    server = build_server(_Bridge(_mm()))
    out = await _call(server, "aegis_monitor", from_handle="p",
                      description="build", done="! pgrep -f 'make build'")
    assert "error" in out
    assert "pgrep -f" in out["error"]
