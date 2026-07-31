"""The `/btw` slash command and the profile it bills to."""
from __future__ import annotations

import pytest

from aegis.btw import SideNote, generation_agent
from aegis.commands import CommandContext, dispatch
from aegis.config import Agent

OPUS = Agent(harness="claude-code", model="opus")
HAIKU = Agent(harness="claude-code", model="haiku")

CONFIG = """
agents:
  opus:
    provider: claude-code
    model: opus
  haiku:
    provider: claude-code
    model: haiku
default_agent: opus
"""


# ---------- which profile pays ------------------------------------------

def test_text_generation_profile_is_used_when_set(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "text_generation: haiku\n")
    agent, unset = generation_agent(OPUS, {"opus": OPUS, "haiku": HAIKU},
                                    root=tmp_path)
    assert agent.model == "haiku"
    assert not unset


def test_falls_back_to_the_session_profile_and_says_so(tmp_path):
    """Unset must be loud: a side note quietly billed at Opus rates is the
    failure the knob exists to prevent."""
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    agent, unset = generation_agent(OPUS, {"opus": OPUS}, root=tmp_path)
    assert agent.model == "opus"
    assert unset


def test_a_missing_config_falls_back_rather_than_raising(tmp_path):
    agent, unset = generation_agent(OPUS, {}, root=tmp_path / "nope")
    assert agent.model == "opus"
    assert unset


# ---------- the command --------------------------------------------------

class StubBridge:
    def __init__(self, note: SideNote | None = None) -> None:
        self.note = note or SideNote(
            answer="core/manager.py", header="last 6 of 47 turns",
            model="haiku", duration_ms=5200, cost_usd=0.0044, ok=True)
        self.called_with: tuple | None = None

    async def side_note(self, handle: str, prompt: str) -> SideNote:
        self.called_with = (handle, prompt)
        return self.note


async def run(line: str, bridge=None):
    bridge = bridge or StubBridge()
    return await dispatch(line, CommandContext(bridge=bridge,
                                               handle="my-tab")), bridge


async def test_btw_answers_the_question():
    result, bridge = await run("btw which file holds the fork guard?")
    assert result.ok
    assert bridge.called_with == ("my-tab", "which file holds the fork guard?")
    assert "core/manager.py" in result.title


async def test_btw_shows_what_the_call_cost():
    result, _ = await run("btw where?")
    assert "haiku" in result.body
    assert "$0.0044" in result.body
    assert "last 6 of 47 turns" in result.body


async def test_a_bare_btw_is_a_typo_not_a_request():
    result, bridge = await run("btw")
    assert not result.ok
    assert "usage" in result.title.lower() or "usage" in result.body.lower()
    assert bridge.called_with is None


async def test_needs_more_points_at_fork():
    """The traded-away capability comes back as a signal, not a guess."""
    bridge = StubBridge(SideNote(
        answer="not in the window", needs_more=True, ok=True,
        header="last 6 of 47 turns", model="haiku"))
    result, _ = await run("btw what does deploy.sh do?", bridge)
    assert result.ok
    assert "/fork" in result.body


async def test_a_failed_note_reports_the_reason():
    bridge = StubBridge(SideNote(ok=False, error="subprocess exploded"))
    result, _ = await run("btw anything?", bridge)
    assert not result.ok
    assert "exploded" in (result.title + result.body)


async def test_btw_is_registered_with_a_usage_line():
    from aegis.commands import REGISTRY
    assert "btw" in REGISTRY
    assert REGISTRY["btw"].usage.startswith("/btw")


@pytest.mark.parametrize("phrase", ["side", "question", "window"])
async def test_btw_help_describes_what_it_does(phrase):
    from aegis.commands import REGISTRY
    assert any(p in REGISTRY["btw"].summary.lower()
               for p in ("side", "question", "window")), phrase


# ---------- the transient render -----------------------------------------

def palette():
    from aegis.tui.themes import INK, aegis_colors
    return aegis_colors(INK)


def test_render_shows_the_answer_and_the_price():
    from aegis.render import render_side_note
    note = SideNote(answer="core/manager.py", header="last 6 of 47 turns",
                    model="haiku", duration_ms=5200, cost_usd=0.0044, ok=True)
    text = render_side_note(note, palette()).plain
    assert "core/manager.py" in text
    assert "haiku" in text and "5.2s" in text and "$0.0044" in text
    assert "last 6 of 47 turns" in text


def test_render_points_at_fork_when_the_window_was_not_enough():
    from aegis.render import render_side_note
    note = SideNote(answer="not in the window", needs_more=True, ok=True,
                    header="last 6 of 47 turns", model="haiku")
    assert "/fork" in render_side_note(note, palette()).plain


def test_render_stays_quiet_about_fork_when_the_window_sufficed():
    from aegis.render import render_side_note
    note = SideNote(answer="core/manager.py", ok=True, model="haiku")
    assert "/fork" not in render_side_note(note, palette()).plain


def test_render_shows_the_reason_a_note_failed():
    from aegis.render import render_side_note
    text = render_side_note(SideNote(ok=False, error="boom"), palette()).plain
    assert "boom" in text


async def test_the_effect_is_json_serializable():
    """The web seam ships `effect` straight out as JSON. A dataclass in
    there breaks /btw on the web client and nowhere else, which is exactly
    the kind of bug that ships."""
    import json
    result, _ = await run("btw where?")
    assert json.loads(json.dumps(result.effect))["kind"] == "side_note"


async def test_the_effect_round_trips_back_into_a_side_note():
    """The TUI rebuilds the dataclass from the dict to render it."""
    result, _ = await run("btw where?")
    assert SideNote(**result.effect["note"]).answer == "core/manager.py"
