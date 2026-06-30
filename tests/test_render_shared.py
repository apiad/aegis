from aegis.events import Result, ToolUse
from aegis.render_shared import (
    KIND_ICON, PLAN_STATUS_GLYPH, diff_window, pathhint, result_parts,
)


def test_kind_icon_and_glyph_tables():
    assert KIND_ICON["read"] == "📖"
    assert KIND_ICON["execute"] == "⌬"
    assert PLAN_STATUS_GLYPH == {
        "completed": "●", "in_progress": "◐", "pending": "○"}


def test_pathhint_prefers_location_tail_with_line():
    ev = ToolUse(name="Read", summary="", kind="read",
                 locations=(("/deep/nested/foo.py", 42),))
    assert pathhint(ev) == "foo.py:42"


def test_pathhint_falls_back_to_summary():
    ev = ToolUse(name="Bash", summary="echo hi", kind="execute")
    assert pathhint(ev) == "echo hi"


def test_diff_window_trims_common_and_reports_change():
    removed, added, elided = diff_window("alpha\nbeta\n", "alpha\nGAMMA\nbeta\n")
    assert removed == []
    assert added == ["GAMMA"]
    assert elided == 0


def test_diff_window_caps_to_max_lines():
    old = "".join(f"old-{i}\n" for i in range(40))
    new = "".join(f"new-{i}\n" for i in range(40))
    removed, added, elided = diff_window(old, new, max_lines=6)
    assert len(removed) + len(added) == 6
    assert elided == (40 + 40) - 6


def test_result_parts_duration_cost_and_stop_reason():
    parts = result_parts(Result(duration_ms=2500, is_error=False,
                                cost_usd=0.05, stop_reason="refusal"))
    assert parts[0] == "done in 2.5s"
    assert "5¢" in parts
    assert "refusal" in parts


def test_result_parts_omits_end_turn():
    parts = result_parts(Result(duration_ms=1000, is_error=False,
                                stop_reason="end_turn"))
    assert all("end_turn" not in p for p in parts)
