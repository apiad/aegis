from aegis.events import TokenUsage
from aegis.tui.metrics import SessionMetrics, context_window_for


def _u(inp=0, cc=0, cr=0, out=0):
    return TokenUsage(input=inp, cache_creation=cc, cache_read=cr, output=out)


# --- context window lookup --------------------------------------------

def test_context_window_for_claude_code_sonnet_default_200k():
    assert context_window_for("claude-code", "sonnet") == 200_000
    assert context_window_for("claude-code", "claude-sonnet-4-6") == 200_000
    assert context_window_for("claude-code", "haiku") == 200_000


def test_context_window_for_claude_code_opus_is_1m():
    assert context_window_for("claude-code", "opus") == 1_000_000
    assert context_window_for("claude-code", "claude-opus-4-7") == 1_000_000


def test_context_window_for_claude_code_1m_variant():
    assert context_window_for("claude-code", "sonnet-1m") == 1_000_000
    assert context_window_for("claude-code", "claude-sonnet-4-5-1m") == 1_000_000


def test_context_window_for_gemini_is_1m():
    assert context_window_for("gemini", "gemini-2.5-pro") == 1_048_576
    assert context_window_for("gemini", "anything") == 1_048_576


def test_context_window_for_opencode_conservative_200k():
    assert context_window_for("opencode", "anthropic/claude-sonnet-4.5") == 200_000


def test_context_window_for_unknown_harness_returns_zero():
    assert context_window_for("nope", "whatever") == 0


# --- ctx segment in render -------------------------------------------

def test_render_omits_ctx_when_window_is_zero():
    m = SessionMetrics()
    assert "ctx" not in m.render(0.0)


def test_render_ctx_uses_last_committed_true_input_idle():
    m = SessionMetrics(context_window=200_000)
    m.commit(_u(inp=2000, cc=10_000, cr=80_000, out=200), 1.0)
    # last_true_input = 92_000; 92_000 / 200_000 = 46%
    s = m.render(0.0)
    assert "ctx 92k (46%)" in s


def test_render_ctx_uses_provisional_p_in_mid_turn():
    m = SessionMetrics(context_window=200_000)
    m.commit(_u(inp=1000, cc=0, cr=50_000, out=100), 1.0)  # last_true_input=51k
    m.observe(_u(inp=2000, cc=20_000, cr=70_000))           # p_in=92_000
    s = m.render(0.0)
    assert "ctx 92k (46%)" in s
    assert s.startswith("~")


def test_commit_updates_last_true_input_but_none_keeps_it():
    m = SessionMetrics(context_window=200_000)
    m.commit(_u(inp=1, cc=0, cr=99, out=10), 1.0)
    assert m.last_true_input == 100
    m.commit(None, 2.0)              # error/no-result — keep prior value
    assert m.last_true_input == 100


# --- token accounting -------------------------------------------------

def test_commit_accumulates_true_input_and_cached_across_turns():
    m = SessionMetrics()
    m.commit(_u(inp=4, cc=10_000, cr=80_000, out=120), 1.0)
    m.commit(_u(inp=2, cc=0, cr=90_000, out=30), 2.0)
    # true input = input + cc + cr, summed across turns
    assert m.c_in == (4 + 10_000 + 80_000) + (2 + 0 + 90_000)
    assert m.c_out == 150
    assert m.c_cached == 170_000


def test_commit_none_adds_no_tokens():
    m = SessionMetrics()
    m.commit(None, 1.0)
    assert m.c_in == 0 and m.c_out == 0 and m.c_cached == 0


def test_observe_is_max_not_sum_then_commit_is_authoritative():
    # The same step's usage repeats across assistant events; summing would
    # treble-count. observe() takes the MAX (monotonic snapshot).
    m = SessionMetrics()
    rep = _u(inp=2, cc=37_801, cr=0, out=34)
    m.observe(rep)
    m.observe(rep)
    m.observe(rep)                       # 3× identical, like the real stream
    assert m.p_in == 37_803              # one step's true_input, not 3×
    assert m._provisional is True
    # render shows it provisionally with a leading ~
    assert m.render(0.0).startswith("~↑")
    # commit replaces provisional with the authoritative result total
    m.commit(_u(inp=4, cc=38_993, cr=37_801, out=261), 1.0)
    assert m.p_in == 0 and m._provisional is False
    assert m.c_in == 4 + 38_993 + 37_801 and m.c_out == 261
    assert not m.render(0.0).startswith("~")


def test_render_true_input_and_cached_pct():
    m = SessionMetrics()
    m.commit(_u(inp=1000, cc=4000, cr=95_000, out=1200), 1.0)
    s = m.render(0.0)
    # true input 100000 → 100k; cached 95000/100000 → 95%
    assert "↑100k (95% cached)" in s
    assert "↓1.2k" in s


def test_render_zero_before_any_turn():
    s = SessionMetrics().render(0.0)
    assert "↑0 (0% cached) ↓0" in s


# --- tools ------------------------------------------------------------

def test_tool_counts_and_error_increment_independently():
    m = SessionMetrics()
    m.record_tool()
    m.record_tool()
    m.record_tool_error()
    assert "⚒ 2 (1 err)" in m.render(0.0)


def test_render_hides_errors_when_zero():
    m = SessionMetrics()
    m.record_tool()
    out = m.render(0.0)
    assert "⚒ 1" in out and "err" not in out


# --- time -------------------------------------------------------------

def test_turn_seconds_in_flight_then_idle():
    m = SessionMetrics()
    m.start_turn(10.0)
    assert m.turn_seconds(13.0) == 3.0
    m.commit(None, 15.0)
    assert m.turn_seconds(99.0) == 5.0      # frozen at last turn


def test_cancel_turn_freezes_time_and_drops_provisional():
    m = SessionMetrics()
    m.start_turn(10.0)
    m.observe(_u(inp=5000, cr=5000))
    m.cancel_turn(14.0)
    assert m.turn_seconds(99.0) == 4.0
    assert m.p_in == 0 and m._provisional is False
    assert m.c_in == 0                      # nothing committed


def test_session_clock_unanchored_then_begin():
    m = SessionMetrics()
    assert m.session_seconds(999.0) == 0.0
    m.begin_session(100.0)
    m.begin_session(200.0)                  # idempotent
    assert m.session_seconds(160.0) == 60.0


def test_time_formatting_tail():
    m = SessionMetrics(session_start=0.0)
    m.start_turn(0.0)
    assert m.render(45.0).endswith("45s / 45s")
    m2 = SessionMetrics(session_start=0.0)
    m2.start_turn(0.0)
    assert m2.render(243.0).endswith("4m03s / 4m03s")
