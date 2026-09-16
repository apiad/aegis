"""A running session reads `recap`, `loop_judge` and `fleet` from `.aegis.yaml`.

Before this, `load_config` parsed all three and nothing carried them to a
session: `AgentSession` hard-coded both booleans, so `recap: false` and
`loop_judge: false` did nothing.
"""
import asyncio
import logging
import os

import pytest

from aegis.config import Agent, FleetConfig
from aegis.config.roots import AegisRoots
from aegis.core.loop_judge import Judgement
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.recap import Recap
from aegis.tui.state import AgentState

from tests.brain import make_brain

MOVED = TurnFacts(repos=(RepoDelta(name="aegis", files_written=1,
                                   commits=(CommitLine("a1", "feat: x"),)),))

OFF = """\
recap: false
loop_judge: false
fleet:
  recap: "on"
  recap_after_s: 5
  recap_interval_s: 45
"""


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...
    async def interrupt(self): ...

    async def events(self):
        if False:
            yield


def _brain(tmp_path, yaml_text=None):
    if yaml_text is not None:
        (tmp_path / ".aegis.yaml").write_text(yaml_text)
    roster = {"opus": Agent(harness="claude-code", model="opus",
                            effort="high", permission="auto")}
    return make_brain(
        roster, "opus",
        make_session=lambda p, u, h, **kw: _FakeHarness(),
        mcp=None,
        roots=AegisRoots.for_project(tmp_path),
    )


def _rewrite(path, text):
    """Write and force a distinct mtime, so a coarse clock cannot hide it."""
    st = path.stat()
    path.write_text(text)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_a_spawned_session_reads_all_three_from_the_file(tmp_path):
    s = _brain(tmp_path, OFF)._sync_spawn("opus")
    assert s.recap_enabled is False
    assert s.loop_judge_enabled is False
    assert s.fleet_config == FleetConfig(recap="on", recap_after_s=5,
                                         recap_interval_s=45)


def test_no_file_means_the_defaults(tmp_path):
    s = _brain(tmp_path)._sync_spawn("opus")
    assert (s.recap_enabled, s.loop_judge_enabled) == (True, True)
    assert s.fleet_config == FleetConfig()


def test_an_edit_takes_effect_without_respawning(tmp_path):
    s = _brain(tmp_path, OFF)._sync_spawn("opus")
    assert s.recap_enabled is False
    _rewrite(tmp_path / ".aegis.yaml", "recap: true\nloop_judge: false\n")
    assert s.recap_enabled is True
    assert s.loop_judge_enabled is False
    assert s.fleet_config == FleetConfig()


def test_a_broken_config_yields_defaults_and_logs_once(tmp_path):
    # The handler goes on the session's own logger rather than through
    # caplog: `aegis_log.open()` sets `propagate = False` on the "aegis"
    # logger and never restores it, so once any earlier test has opened the
    # log, nothing from aegis.core.session reaches the root handler caplog
    # listens on. This test passed alone and failed after test_aegis_log.py.
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("aegis.core.session")
    handler, level = _Keep(logging.WARNING), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        s = _brain(tmp_path, "recap: false\nfleet:\n  recap: sometimes\n")._sync_spawn("opus")
        assert s.recap_enabled is True
        assert s.loop_judge_enabled is True
        assert s.fleet_config == FleetConfig()
        assert s.recap_enabled is True
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    hits = [r for r in records if ".aegis.yaml" in r.getMessage()
            or "config" in r.getMessage()]
    assert len(hits) == 1


def test_two_reads_parse_the_yaml_once(tmp_path, monkeypatch):
    s = _brain(tmp_path, OFF)._sync_spawn("opus")
    import aegis.config.yaml_loader as yl

    calls = []
    real = yl.load_config
    monkeypatch.setattr(yl, "load_config",
                        lambda root: calls.append(root) or real(root))
    s.recap_enabled
    s.loop_judge_enabled
    s.fleet_config
    s.recap_enabled
    assert len(calls) <= 1


def test_an_assigned_flag_overrides_the_file(tmp_path):
    """Existing tests pin behaviour with `s.recap_enabled = True`."""
    s = _brain(tmp_path, OFF)._sync_spawn("opus")
    s.recap_enabled = True
    s.loop_judge_enabled = True
    assert (s.recap_enabled, s.loop_judge_enabled) == (True, True)


def _facts_returning(facts):
    async def _build(**_kw):
        return facts
    return _build


@pytest.mark.asyncio
async def test_recap_false_stops_the_turn_recap(tmp_path, monkeypatch):
    fired = []

    async def recap(**_kw):
        fired.append(1)
        return Recap(line="x", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", recap)
    s = _brain(tmp_path, OFF)._sync_spawn("opus")
    # _sync_spawn passes no roster, and a session without one never recaps
    # at all; give it one so the only thing standing in the way is the flag.
    # Tripwire: _sync_spawn threads no roster today. The day it does, this
    # fails, and the patch below must go so the test runs the real path.
    assert s._agents is None, "roster now threaded: drop this patch"
    s._agents = {}
    monkeypatch.setattr(s.digest, "build", _facts_returning(MOVED))
    s._maybe_recap(MOVED)
    assert s._recap_task is None
    await asyncio.sleep(0)
    assert fired == []


@pytest.mark.asyncio
async def test_recap_true_still_fires(tmp_path, monkeypatch):
    """The control: same path, flag on, and the recap task exists."""
    async def recap(**_kw):
        return Recap(line="x", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", recap)
    s = _brain(tmp_path, "recap: true\n")._sync_spawn("opus")
    # Tripwire: _sync_spawn threads no roster today. The day it does, this
    # fails, and the patch below must go so the test runs the real path.
    assert s._agents is None, "roster now threaded: drop this patch"
    s._agents = {}
    s._maybe_recap(MOVED)
    assert s._recap_task is not None
    s._cancel_recap()


async def _settle(session):
    for _ in range(200):
        await asyncio.sleep(0)
        if session.state is not AgentState.working:
            return


@pytest.mark.asyncio
async def test_loop_judge_false_never_consults_the_judge(tmp_path, monkeypatch):
    asked = []

    async def verdict(**_kw):
        asked.append(1)
        return Judgement(verdict="done", reason="x", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s = _brain(tmp_path, OFF)._sync_spawn("opus")
    # Tripwire: _sync_spawn threads no roster today. The day it does, this
    # fails, and the patch below must go so the test runs the real path.
    assert s._agents is None, "roster now threaded: drop this patch"
    s._agents = {}
    s.arm_loop("keep going", 3)
    await _settle(s)
    await _settle(s)
    assert asked == []


@pytest.mark.asyncio
async def test_loop_judge_true_consults_the_judge(tmp_path, monkeypatch):
    asked = []

    async def verdict(**_kw):
        asked.append(1)
        return Judgement(verdict="done", reason="x", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s = _brain(tmp_path, "loop_judge: true\n")._sync_spawn("opus")
    # Tripwire: _sync_spawn threads no roster today. The day it does, this
    # fails, and the patch below must go so the test runs the real path.
    assert s._agents is None, "roster now threaded: drop this patch"
    s._agents = {}
    s.arm_loop("keep going", 3)
    await _settle(s)
    await _settle(s)
    assert asked


@pytest.mark.parametrize("key", ["recap", "loop_judge"])
@pytest.mark.parametrize("value", ['"false"', "'no'", "0", "off"])
def test_a_flag_that_is_not_a_bool_is_a_config_error(tmp_path, key, value):
    """`bool(raw.get(...))` turned the quoted string "false" into True. That
    was harmless while the flags were never read; now that they are, a
    quoted false would silently leave a paid call switched on."""
    from aegis.config.yaml_loader import ConfigError, load_config

    (tmp_path / ".aegis.yaml").write_text(f"{key}: {value}\n")
    with pytest.raises(ConfigError, match=key):
        load_config(tmp_path)


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_a_real_bool_flag_loads(tmp_path, value, expected):
    from aegis.config.yaml_loader import load_config

    (tmp_path / ".aegis.yaml").write_text(f"recap: {value}\nloop_judge: {value}\n")
    cfg = load_config(tmp_path)
    assert (cfg.recap, cfg.loop_judge) == (expected, expected)
