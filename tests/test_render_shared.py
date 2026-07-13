from aegis.events import Result, ToolUse
from aegis.render_shared import (
    KIND_ICON, PLAN_STATUS_GLYPH, describe_tool, diff_window,
    format_tool_args, pathhint, result_parts,
)


def test_format_args_bash_shows_command_and_description_comment():
    out = format_tool_args("Bash", {"command": "ls -la",
                                    "description": "list files"})
    assert "# list files" in out
    assert "ls -la" in out


def test_format_args_generic_key_value():
    out = format_tool_args("Grep", {"pattern": "foo", "path": "src"})
    assert "pattern: foo" in out and "path: src" in out


def test_format_args_caps_long_values():
    out = format_tool_args("X", {"blob": "y" * 900})
    assert out.endswith("…") and len(out) < 900


def test_format_args_falls_back_to_summary():
    assert format_tool_args("Bash", None, summary="echo hi") == "echo hi"


def test_describe_bash_prefers_description_then_command():
    d = describe_tool("Bash", {"command": "uv run pytest -q",
                               "description": "Run tests"})
    assert d.startswith("Run tests")
    assert "uv run pytest -q" in d


def test_describe_bash_command_only():
    assert describe_tool("Bash", {"command": "git status -sb"}) \
        == "git status -sb"


def test_describe_bash_truncates_long_command():
    long = "x" * 200
    d = describe_tool("Bash", {"command": long})
    assert len(d) < 200 and d.endswith("…")


def test_describe_read_uses_filename_tail():
    assert describe_tool("Read",
                         {"file_path": "/a/b/render.py"}) == "read render.py"


def test_describe_edit_shows_file_and_old_snippet():
    d = describe_tool("Edit", {"file_path": "pane.py",
                               "old_string": "def foo(): pass"})
    assert d.startswith("edit pane.py")


def test_describe_grep_shows_pattern():
    d = describe_tool("Grep", {"pattern": "render_event", "path": "src/aegis"})
    assert "render_event" in d and "aegis" in d


def test_describe_task_names_the_subagent_work():
    d = describe_tool("Task", {"description": "Search code",
                               "subagent_type": "Explore"})
    assert "Search code" in d


def test_describe_unknown_tool_uses_first_string_arg():
    assert describe_tool("SlackPost",
                         {"channel": "general", "text": "hi"}) == "general"


def test_describe_falls_back_to_summary_when_no_raw_input():
    # Compact-wire safety: raw_input stripped, but summary survives.
    assert describe_tool("Bash", None, summary="echo hi") == "echo hi"


def test_describe_falls_back_to_location_tail():
    d = describe_tool("Read", None, locations=(("/deep/foo.py", 10),))
    assert d == "read foo.py:10"


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
