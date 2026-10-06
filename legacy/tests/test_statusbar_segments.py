from aegis.themes import AegisColors
from aegis.tui.state import AgentState
from aegis.tui.widgets import StatusBar, short_model

COLORS = AegisColors(
    ready="green", working="yellow", error="red", accent="blue",
    muted="grey50", ok="green", err="red", user="blue", user_bg="black")


def test_short_model_strips_prefix_and_dots_the_version():
    assert short_model("claude-opus-4-8") == "opus-4.8"
    assert short_model("claude-sonnet-5") == "sonnet-5"
    assert short_model("gpt-5") == "gpt-5"


def _bar():
    bar = StatusBar("claude-opus-4-8", "high", COLORS)
    bar.set_state(AgentState.working)
    bar.set_metrics(("METRICS-FULL", "METRICS-MID", "METRICS-SHORT"))
    bar.set_system(("CPU 1% · RAM 2% · DSK 3%", "1·2·3%"))
    return bar


def test_unmeasured_bar_renders_everything():
    text = _bar().render_plain()
    assert "aegis" in text
    assert "claude-opus-4-8" in text
    assert "METRICS-FULL" in text
    assert "CPU 1%" in text


def test_setters_accept_a_bare_string():
    bar = _bar()
    bar.set_metrics("JUST-ONE")
    assert "JUST-ONE" in bar.render_plain()


def test_loop_segment_hidden_when_none():
    bar = _bar()
    bar.set_loop(None)
    assert "loop" not in bar.render_plain()
    bar.set_loop({"iteration": 3, "max_iterations": 20})
    assert "⟳ loop 3/20" in bar.render_plain()


def test_quota_segment_hidden_when_empty():
    bar = _bar()
    bar.set_quota(())
    assert "⧗" not in bar.render_plain()
    bar.set_quota(("⧗ 5h 64% · wk 7%", "⧗ 64/7%"))
    assert "⧗ 5h 64%" in bar.render_plain()


def test_system_is_dropped_before_identity_when_narrow():
    bar = _bar()
    bar._width_override = 60
    bar._refresh()
    text = bar.render_plain()
    assert "CPU 1%" not in text
    assert "opus" in text


def test_state_always_survives():
    bar = _bar()
    bar._width_override = 12
    bar._refresh()
    assert "working" in bar.render_plain()
