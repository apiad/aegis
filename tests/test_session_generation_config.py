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
  recap: "off"
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
    assert s.fleet_config == FleetConfig(recap="off", recap_after_s=5,
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
    # The session carries the brain's roster, so nothing is patched: the
    # only thing standing between a moved turn and a paid call is the flag.
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
    s.arm_loop("keep going", 3)
    await _settle(s)
    await _settle(s)
    assert asked == []


@pytest.mark.asyncio
async def test_loop_judge_true_consults_the_judge(tmp_path, monkeypatch):
    asked = []

    async def verdict(**kw):
        asked.append(kw.get("root"))
        return Judgement(verdict="done", reason="x", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s = _brain(tmp_path, "loop_judge: true\n")._sync_spawn("opus")
    s.arm_loop("keep going", 3)
    await _settle(s)
    await _settle(s)
    assert asked
    # The judge resolves its billing profile from the session's config root,
    # not the cwd — the same rule as the turn recap.
    assert asked[0] == s._config_root


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


HAIKU = """\
agents:
  opus:
    provider: claude-code
    model: opus
  haiku:
    provider: claude-code
    model: claude-haiku-4-5-20251001
    effort: low
    permission: read
default_agent: opus
text_generation: haiku
"""


def _brain_with_haiku(tmp_path, yaml_text):
    (tmp_path / ".aegis.yaml").write_text(yaml_text)
    roster = {
        "opus": Agent(harness="claude-code", model="opus", effort="high", permission="auto"),
        "haiku": Agent(harness="claude-code", model="claude-haiku-4-5-20251001",
                       effort="low", permission="read"),
    }
    return make_brain(
        roster, "opus",
        make_session=lambda p, u, h, **kw: _FakeHarness(),
        mcp=None,
        roots=AegisRoots.for_project(tmp_path),
    )


def test_a_spawned_session_carries_the_brains_roster(tmp_path):
    """Before this no construction site passed agents=, so every real
    session had _agents None and the turn recap and loop judge returned
    before doing anything — since they were written. It is the brain's own
    dict, so a profile hot-registered later is visible too."""
    mgr = _brain(tmp_path, "recap: true\n")
    s = mgr._sync_spawn("opus")
    assert s._agents is mgr._agents


def _decoy_cwd(tmp_path, monkeypatch):
    """A cwd whose own .aegis.yaml names no text_generation, so a resolver
    that walks up from the cwd bills to opus and one that uses the session's
    config root bills to haiku."""
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / ".aegis.yaml").write_text(
        "agents:\n  opus:\n    provider: claude-code\n    model: opus\ndefault_agent: opus\n")
    monkeypatch.chdir(decoy)


async def test_the_turn_recap_bills_to_text_generation_from_the_config_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    s = _brain_with_haiku(root, HAIKU)._sync_spawn("opus")
    _decoy_cwd(tmp_path, monkeypatch)
    billed = []

    class _Driver:
        supports_oneshot = True

        async def generate_detailed(self, agent, cwd, schema, *instructions):
            billed.append(agent.model)
            from aegis.drivers.oneshot import Generation
            return Generation()

    monkeypatch.setattr("aegis.drivers.get_driver", lambda harness: _Driver())
    monkeypatch.setattr("aegis.state.session_log.replay_events",
                        lambda state_dir, log_id: type("R", (), {"events": [], "stamps": []})())
    await s._run_recap(MOVED)
    assert billed == ["claude-haiku-4-5-20251001"]


async def test_the_loop_judge_bills_to_text_generation_from_the_config_root(tmp_path, monkeypatch):
    from aegis.core import loop_judge

    root = tmp_path / "root"
    root.mkdir()
    s = _brain_with_haiku(root, HAIKU)._sync_spawn("opus")
    _decoy_cwd(tmp_path, monkeypatch)
    resolved = []
    real = __import__("aegis.btw", fromlist=["generation_agent"]).generation_agent

    def spy(fallback, agents, root=None):
        agent, unset = real(fallback, agents, root)
        resolved.append(agent.model)
        return agent, unset

    monkeypatch.setattr("aegis.btw.generation_agent", spy)
    monkeypatch.setattr("aegis.drivers.get_driver", lambda harness: (_ for _ in ()).throw(KeyError(harness)))
    await loop_judge.judge_for(
        state_dir=s.state_dir, log_id=s.log_id, instruction="x", iteration=1,
        max_iterations=20, facts=TurnFacts(), still_streak=0, advisory="",
        agent=s.agent, agents=s._agents, cwd=str(s.project_root),
        root=s._config_root,
    )
    assert resolved == ["claude-haiku-4-5-20251001"]
