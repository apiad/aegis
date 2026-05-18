from aegis.events import Result
from aegis.tui.metrics import SessionMetrics


def _r(inp=None, out=None):
    return Result(duration_ms=0, is_error=False,
                  input_tokens=inp, output_tokens=out)


def test_tokens_accumulate_across_turns():
    m = SessionMetrics(0.0)
    m.start_turn(0.0)
    m.end_turn(_r(100, 20), 1.0)
    m.start_turn(2.0)
    m.end_turn(_r(50, 5), 3.0)
    assert m.in_tokens == 150
    assert m.out_tokens == 25


def test_missing_tokens_contribute_zero():
    m = SessionMetrics(0.0)
    m.start_turn(0.0)
    m.end_turn(_r(None, None), 1.0)
    assert m.in_tokens == 0 and m.out_tokens == 0


def test_tool_counts_and_error_increments_both():
    m = SessionMetrics(0.0)
    m.record_tool()
    m.record_tool()
    m.record_tool_error()
    assert m.tool_calls == 2
    assert m.tool_errors == 1


def test_turn_seconds_in_flight_then_idle():
    m = SessionMetrics(0.0)
    m.start_turn(10.0)
    assert m.turn_seconds(13.0) == 3.0
    m.end_turn(_r(), 15.0)
    assert m.turn_seconds(99.0) == 5.0


def test_session_seconds_monotonic():
    m = SessionMetrics(100.0)
    assert m.session_seconds(160.0) == 60.0


def test_cancel_turn_freezes_time_no_tokens():
    m = SessionMetrics(0.0)
    m.start_turn(10.0)
    m.cancel_turn(14.0)
    assert m.turn_seconds(999.0) == 4.0
    assert m.in_tokens == 0 and m.out_tokens == 0


def test_render_hides_errors_when_zero():
    m = SessionMetrics(0.0)
    m.record_tool()
    out = m.render(0.0)
    assert "⚒ 1" in out
    assert "err" not in out


def test_render_shows_errors_when_nonzero():
    m = SessionMetrics(0.0)
    m.record_tool()
    m.record_tool_error()
    assert "⚒ 1 (1 err)" in m.render(0.0)


def test_token_humanization_boundaries():
    m = SessionMetrics(0.0)
    m.start_turn(0.0)
    m.end_turn(_r(999, 1000), 0.0)
    s = m.render(0.0)
    assert "↑999" in s
    assert "↓1k" in s
    m.start_turn(0.0)
    m.end_turn(_r(235, 999_001), 0.0)
    s = m.render(0.0)
    assert "↑1.2k" in s
    assert "↓1.0M" in s


def test_time_formatting():
    m = SessionMetrics(0.0)
    m.start_turn(0.0)
    assert m.render(45.0).endswith("45s / 45s")
    m2 = SessionMetrics(0.0)
    m2.start_turn(0.0)
    assert m2.render(243.0).endswith("4m03s / 4m03s")


def test_render_before_first_turn_is_zero_seconds():
    m = SessionMetrics(0.0)
    assert "0s / 0s" in m.render(0.0)
