"""What the window behind a tool call shows.

The transcript row carries a label, a verdict and a duration. Everything
it cannot hold — the whole command, the whole diff, the whole output —
has to be here, or the collapse lost information rather than hiding it.
"""

from rich.console import Console

from aegis.events import ToolResult, ToolUse
from aegis.tui.themes import INK, aegis_colors
from aegis.tui.tool_detail import OUTPUT_MAX_ROWS, detail_body

C = aegis_colors(INK)


def as_text(renderable, width=100) -> str:
    con = Console(record=True, width=width)
    con.print(renderable)
    return con.export_text()


def test_body_shows_the_full_command_not_the_500_char_cap():
    cmd = "echo " + "x" * 900
    use = ToolUse(
        name="Bash",
        summary="",
        kind="execute",
        raw_input={"description": "long", "command": cmd},
    )
    body, dropped = detail_body(use, ToolResult(text="ok", is_error=False), C)
    assert "x" * 900 in as_text(body, width=1200).replace("\n", "")
    assert dropped == 0


def test_body_shows_the_full_output():
    out = "\n".join(f"line{i}" for i in range(50))
    use = ToolUse(name="Bash", summary="", kind="execute", raw_input={"command": "seq"})
    body, dropped = detail_body(use, ToolResult(text=out, is_error=False), C)
    rendered = as_text(body)
    assert "line0" in rendered and "line49" in rendered
    assert dropped == 0


def test_a_huge_output_is_capped_and_says_how_much_it_dropped():
    # max_rows is passed explicitly, and the numbers below are literals: a
    # test that derives its input from the constant it is checking passes
    # for any value of that constant, including none at all.
    out = "\n".join(f"line{i}" for i in range(25))
    use = ToolUse(name="Bash", summary="", kind="execute", raw_input={"command": "seq"})
    body, dropped = detail_body(
        use, ToolResult(text=out, is_error=False), C, max_rows=10
    )
    assert dropped == 15
    rendered = as_text(body)
    assert "15 more lines" in rendered
    assert "line9" in rendered
    assert "line10" not in rendered


def test_the_default_cap_is_two_thousand_rows():
    # Textual lays out every mounted row; the cap is what keeps a 50k-line
    # result from costing a full-screen reflow to show a screenful.
    assert OUTPUT_MAX_ROWS == 2000
    out = "\n".join(f"line{i}" for i in range(OUTPUT_MAX_ROWS + 1))
    use = ToolUse(name="Bash", summary="", kind="execute", raw_input={"command": "seq"})
    _, dropped = detail_body(use, ToolResult(text=out, is_error=False), C)
    assert dropped == 1


def test_an_edit_shows_its_whole_diff_not_a_six_row_preview():
    old = "\n".join(f"old{i}" for i in range(20))
    new = "\n".join(f"new{i}" for i in range(20))
    use = ToolUse(
        name="Edit",
        summary="",
        kind="edit",
        raw_input={"file_path": "/a/pane.py", "old_string": old, "new_string": new},
    )
    res = ToolResult(text="ok", is_error=False, diff=("/a/pane.py", old, new))
    body, _ = detail_body(use, res, C)
    rendered = as_text(body)
    assert "old19" in rendered and "new19" in rendered
    assert "more line" not in rendered


def test_a_running_call_has_no_output_section():
    use = ToolUse(
        name="Bash", summary="", kind="execute", raw_input={"command": "sleep 9"}
    )
    body, dropped = detail_body(use, None, C)
    rendered = as_text(body)
    assert "sleep 9" in rendered
    assert "still running" in rendered
    assert dropped == 0
