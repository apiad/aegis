from aegis.tui.groups.state import GroupTabState, aggregate_state_emoji


def test_aggregate_idle_all_done_is_check():
    assert aggregate_state_emoji([("a", "idle"), ("b", "idle")]) == "✓"


def test_aggregate_any_busy_is_hourglass():
    assert aggregate_state_emoji([("a", "idle"), ("b", "busy")]) == "⏳"


def test_aggregate_any_error_is_warn():
    assert aggregate_state_emoji([("a", "idle"), ("b", "errored")]) == "⚠"


def test_aggregate_any_lost_is_blocked():
    assert aggregate_state_emoji([("a", "lost"), ("b", "idle")]) == "⛔"


def test_group_tab_state_label_counts_busy():
    s = GroupTabState(
        name="alpha",
        member_states=[("a", "busy"), ("b", "idle"), ("c", "busy")])
    assert s.total == 3
    assert s.active == 2
    assert s.emoji == "⏳"
    assert s.tab_label() == "▣ alpha [2/3 ⏳]"


def test_group_tab_state_empty_label():
    s = GroupTabState(name="empty")
    assert s.total == 0
    assert s.active == 0
    assert s.emoji == "✓"
    assert s.tab_label() == "▣ empty [0/0 ✓]"
