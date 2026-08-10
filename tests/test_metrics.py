from aegis.events import TokenUsage
from aegis.tui.metrics import SessionMetrics, context_window_for


def _u(inp=0, cc=0, cr=0, out=0):
    return TokenUsage(input=inp, cache_creation=cc, cache_read=cr, output=out)


# --- context window lookup --------------------------------------------

def test_context_window_for_claude_code_per_model():
    """Sonnet 4.6 + Opus 4.7 both have 1M context windows; only Haiku
    stays at 200k (per Anthropic docs + models.dev). The provider's
    200k default is what unknown-model fallbacks land on."""
    assert context_window_for("claude-code", "sonnet") == 1_000_000
    assert context_window_for("claude-code", "claude-sonnet-4-6") == 1_000_000
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


# --- cost segment in render -------------------------------------------

def test_render_cost_omitted_without_provider_model():
    """No provider/model wiring → status line stays cost-free."""
    m = SessionMetrics()
    m.commit(_u(inp=1000, cc=0, cr=0, out=500), 1.0)
    s = m.render(0.0)
    assert "$" not in s and "¢" not in s


def test_render_cost_shown_with_provider_model():
    """Opus 4.7 at $5/$25 per MTok. 1k input + 500 output ≈
    1000*5/1M + 500*25/1M = 0.005 + 0.0125 = $0.0175 → "1.8¢"."""
    m = SessionMetrics(provider="claude-code", model="opus")
    m.commit(_u(inp=1000, cc=0, cr=0, out=500), 1.0)
    s = m.render(0.0)
    assert "¢" in s or "$" in s


def test_render_cost_uses_cents_below_one_dollar():
    """The status-line formatter shows cents for sub-dollar costs so
    short sessions stay visually informative."""
    m = SessionMetrics(provider="claude-code", model="haiku")
    m.commit(_u(inp=10_000, cc=0, cr=0, out=5_000), 1.0)
    # haiku at $1/$5 → 10_000*1/1M + 5_000*5/1M = 0.01 + 0.025 = $0.035 = 3.5¢
    s = m.render(0.0)
    assert "3" in s and "¢" in s


def test_render_cost_uses_dollars_above_one():
    """At >= $1 the status line switches to dollar formatting."""
    m = SessionMetrics(provider="claude-code", model="opus")
    m.commit(_u(inp=1_000_000, cc=0, cr=0, out=0), 1.0)
    # opus $5/MTok input → exactly $5.00
    s = m.render(0.0)
    assert "$5" in s


def test_render_cost_silent_on_unknown_model():
    """An unknown model must NOT crash the render — cost is dropped."""
    m = SessionMetrics(provider="claude-code", model="made-up-model-9")
    m.commit(_u(inp=1000, cc=0, cr=0, out=500), 1.0)
    s = m.render(0.0)
    assert "$" not in s and "¢" not in s


def test_cost_tracks_cache_creation_and_cache_read_separately():
    """Cache-creation (write rate) and cache-read (hit rate) tokens
    must be tallied separately — both are subtracted from c_in to
    derive the uncached input."""
    m = SessionMetrics(provider="claude-code", model="opus")
    m.commit(_u(inp=1000, cc=2000, cr=3000, out=500), 1.0)
    assert m.input_tokens == 1000
    assert m.cache_write_tokens == 2000
    assert m.cache_hit_tokens == 3000
    assert m.output_tokens == 500


def test_time_formatting_tail():
    m = SessionMetrics(session_start=0.0)
    m.start_turn(0.0)
    assert m.render(45.0).endswith("45s / 45s")
    m2 = SessionMetrics(session_start=0.0)
    m2.start_turn(0.0)
    assert m2.render(243.0).endswith("4m03s / 4m03s")


# --- tok/s meter (rolling generation speed) --------------------------

def _turn(m, out, seconds):
    """Run one turn of `seconds` wall-clock that generated `out` tokens."""
    m.start_turn(0.0)
    m.commit(_u(out=out), float(seconds))


def test_recent_tps_none_before_any_turn():
    m = SessionMetrics()
    assert m.recent_tps() is None
    assert "tok/s" not in m.render(0.0)


def test_recent_tps_single_turn():
    m = SessionMetrics()
    _turn(m, out=100, seconds=2)      # 100 tok / 2 s = 50 tok/s
    assert m.recent_tps() == 50.0
    assert "⚡ 50 tok/s" in m.render(0.0)


def test_recent_tps_is_token_weighted_over_turns():
    m = SessionMetrics()
    _turn(m, out=100, seconds=2)      # 50 tok/s
    _turn(m, out=300, seconds=2)      # 150 tok/s
    # token-weighted: (100+300) / (2+2) = 100 tok/s, not (50+150)/2
    assert m.recent_tps() == 100.0


def test_recent_tps_window_caps_at_five_turns():
    m = SessionMetrics()
    _turn(m, out=10_000, seconds=1)   # huge, should fall out of the window
    for _ in range(5):
        _turn(m, out=50, seconds=1)   # 50 tok/s each
    # only the last 5 counted: 250 tok / 5 s = 50 tok/s
    assert m.recent_tps() == 50.0


def test_recent_tps_skips_zero_duration_and_zero_output():
    m = SessionMetrics()
    m.commit(_u(out=100), 1.0)        # no start_turn → duration 0, skipped
    _turn(m, out=0, seconds=2)        # zero output, skipped
    assert m.recent_tps() is None
    _turn(m, out=80, seconds=2)       # 40 tok/s
    assert m.recent_tps() == 40.0


# --- thinking-token breakdown (% think) -------------------------------

def test_think_segment_shows_share_of_output():
    m = SessionMetrics()
    m.commit(_u(out=1000), 1.0)       # 1000 output tokens
    m.observe_thinking(600)           # 600 of them were reasoning
    m.observe_thinking(200)           # → 800 total think
    out = m.render(0.0)
    assert "↓1k (80% think)" in out


def test_think_segment_absent_when_no_thinking():
    m = SessionMetrics()
    m.commit(_u(out=1000), 1.0)
    assert "think" not in m.render(0.0)


def test_thinking_tokens_property_stays_zero_for_cost():
    # Reasoning is billed inside output — cost view must not double-count.
    m = SessionMetrics()
    m.observe_thinking(500)
    assert m.c_think == 500
    assert m.thinking_tokens == 0


def _loaded_metrics():
    m = SessionMetrics()
    m.c_in, m.c_out, m.c_cached, m.c_think = 70000, 12000, 56000, 9600
    m.tool_calls, m.tool_errors = 27, 1
    m.context_window, m.last_true_input = 200000, 88200
    return m


def test_render_tiers_widest_equals_render():
    m = _loaded_metrics()
    assert m.render_tiers(0.0)[0] == m.render(0.0)


def test_render_tiers_narrow_monotonically():
    from aegis.tui.fit import plain_width
    tiers = _loaded_metrics().render_tiers(0.0)
    widths = [plain_width(t) for t in tiers]
    assert widths == sorted(widths, reverse=True)
    assert len(tiers) == 4


def test_tier1_drops_tools():
    tiers = _loaded_metrics().render_tiers(0.0)
    assert "⚒" in tiers[0]
    assert "⚒" not in tiers[1]


def test_tier2_drops_shares_and_shortens_ctx():
    tiers = _loaded_metrics().render_tiers(0.0)
    assert "cached" in tiers[1]
    assert "cached" not in tiers[2]
    assert "think" not in tiers[2]
    assert "ctx 44%" in tiers[2]


def test_tier3_is_the_irreducible_core():
    tiers = _loaded_metrics().render_tiers(0.0)
    assert "ctx" not in tiers[3]
    assert "↑" in tiers[3] and "↓" in tiers[3]


# --- context gauge: peak sub-turn, not the accumulated total ----------

def test_commit_keeps_per_subturn_peak_not_the_accumulated_total():
    """Result.usage.true_input accumulates across every sub-turn of an
    agentic turn, so it is not a context size. The gauge must keep the
    largest single sub-turn instead.

    Numbers are a scaled-down real turn: three sub-turns at 97k/113k/123k
    commit a Result of 333k. Measured over 6871 real turns, using the
    Result value put 61.9% of them above 100% of the window."""
    m = SessionMetrics(context_window=200_000)
    for ti in (97_000, 113_000, 123_000):
        m.observe(_u(inp=ti))
    m.commit(_u(inp=333_000, out=500), now=1.0)

    assert m.last_true_input == 123_000     # the peak sub-turn
    assert m.c_in == 333_000                # cumulative accounting unchanged


def test_commit_falls_back_to_result_when_no_streaming_usage():
    """A turn with no streamed usage (short turn, or a harness that does
    not stream it) still has to show something."""
    m = SessionMetrics(context_window=200_000)
    m.commit(_u(inp=42_000, out=100), now=1.0)
    assert m.last_true_input == 42_000


def test_observe_context_is_the_shared_funnel():
    """Both the Claude streaming path and the ACP ContextUpdate path feed
    one method, so the gauge behaves identically per harness."""
    m = SessionMetrics(context_window=200_000)
    m.observe_context(50_000)
    m.observe_context(30_000)      # a smaller snapshot never lowers the peak
    assert m.p_in == 50_000
    m.observe_context(80_000)
    assert m.p_in == 80_000


def test_gauge_stays_under_100_percent_on_a_long_agentic_turn():
    """Regression for the 1000%+ bug. A 30-sub-turn turn averaging 70k
    commits a ~2.1M Result; the gauge must still read the peak sub-turn."""
    m = SessionMetrics(context_window=200_000)
    total = 0
    for i in range(30):
        ti = 60_000 + i * 700
        total += ti
        m.observe(_u(inp=ti))
    m.commit(_u(inp=total, out=1000), now=1.0)
    pct = round(100 * m.last_true_input / m.context_window)
    assert pct <= 100, f"gauge read {pct}%"
    assert m.last_true_input == 60_000 + 29 * 700


# --- compaction: authoritative, from the harness boundary -------------

def test_note_compaction_rebaselines_the_gauge():
    """After compaction the model sees a summary, not the old context.
    p_in still holds the pre-compaction peak (~the full window), so
    without a re-baseline the gauge would read ~100% for the rest of
    the turn."""
    m = SessionMetrics(context_window=1_000_000)
    m.observe(_u(inp=999_917))
    m.note_compaction(post_tokens=15_022)

    assert m.compaction_count == 1
    assert m.p_in == 15_022
    assert m.last_true_input == 15_022


def test_note_compaction_lets_the_gauge_climb_again():
    """The re-baseline is a floor reset, not a lock -- growth after the
    boundary must register normally."""
    m = SessionMetrics(context_window=1_000_000)
    m.observe(_u(inp=999_917))
    m.note_compaction(post_tokens=15_022)
    m.observe(_u(inp=40_000))
    assert m.p_in == 40_000


def test_note_compaction_without_tokens_does_not_zero_the_gauge():
    """A boundary with no metadata still counts, but must not wipe the
    gauge -- an empty payload is missing information, not a 0-token
    context."""
    m = SessionMetrics(context_window=1_000_000)
    m.observe(_u(inp=50_000))
    m.note_compaction(post_tokens=0)
    assert m.compaction_count == 1
    assert m.p_in == 50_000


def test_compaction_count_accumulates_across_turns():
    """The counter is session-scoped: it is a context-integrity signal,
    not a per-turn one, so commit() must not reset it."""
    m = SessionMetrics(context_window=1_000_000)
    m.note_compaction(post_tokens=15_000)
    m.commit(_u(inp=100, out=10), now=1.0)
    m.note_compaction(post_tokens=20_000)
    assert m.compaction_count == 2


# --- ctx segment colour ------------------------------------------------

def test_ctx_segment_is_uncoloured_below_half():
    m = SessionMetrics(context_window=200_000, last_true_input=40_000)
    t0 = m.render_tiers(now=0.0)[0]
    assert "ctx 40k (20%)" in t0
    assert "[$warning]" not in t0 and "[$error]" not in t0


def test_ctx_segment_warns_at_half_full():
    m = SessionMetrics(context_window=200_000, last_true_input=120_000)
    t0 = m.render_tiers(now=0.0)[0]
    assert "[$warning]ctx 120k (60%)[/$warning]" in t0


def test_ctx_segment_errors_at_three_quarters():
    m = SessionMetrics(context_window=200_000, last_true_input=160_000)
    t0 = m.render_tiers(now=0.0)[0]
    assert "[$error]ctx 160k (80%)[/$error]" in t0


def test_ctx_short_tier_is_coloured_too():
    """T2 reduces ctx to its percentage -- the part you act on -- so it
    is exactly the tier that must keep the warning."""
    m = SessionMetrics(context_window=200_000, last_true_input=160_000)
    assert "[$error]ctx 80%[/$error]" in m.render_tiers(now=0.0)[2]


def test_ctx_colour_does_not_consume_width_budget():
    """fit.plain_width strips markup before measuring, so a coloured
    segment must measure the same as its visible text."""
    from aegis.tui.fit import plain_width
    hot = SessionMetrics(context_window=200_000, last_true_input=160_000)
    cool = SessionMetrics(context_window=200_000, last_true_input=160_00)
    t0_hot = hot.render_tiers(now=0.0)[0]
    assert plain_width(t0_hot) == len(
        t0_hot.replace("[$error]", "").replace("[/$error]", ""))
    # And the tag itself costs nothing relative to identical plain text.
    assert plain_width(t0_hot) > plain_width(cool.render_tiers(now=0.0)[0]) - 10


def test_render_tiers_still_returns_four_tiers():
    """StatusBar.set_metrics feeds the tuple straight into _tiers(), which
    treats every element as a tier -- a fifth element would render as a
    fifth, narrower variant of the whole bar."""
    m = SessionMetrics(context_window=200_000, last_true_input=160_000)
    assert len(m.render_tiers(now=0.0)) == 4
