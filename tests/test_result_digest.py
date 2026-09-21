"""The short verdict a one-row tool line carries after its glyph.

The row it replaces printed the first 100 characters of the output, which
is where a Read shows its imports and a Bash shows a progress bar. These
tests pin what each tool's row says instead.
"""

from aegis.events import ToolResult
from aegis.render_shared import diff_counts, result_digest


def test_diff_counts_ignores_common_prefix_and_suffix():
    # diff_window caps at six rows and elides context; a COUNT must see
    # every changed line, so this pair has more changes than that budget.
    old = "\n".join(["same"] + [f"old{i}" for i in range(9)] + ["tail"])
    new = "\n".join(["same"] + [f"new{i}" for i in range(9)] + ["tail"])
    assert diff_counts(old, new) == (9, 9)


def test_edit_digest_reads_plus_minus():
    r = ToolResult(text="ok", is_error=False, diff=("pane.py", "a\nb\nc", "a\nB\nc\nd"))
    assert result_digest("Edit", r) == "+2 −1"


def test_write_digest_has_no_removed_side():
    r = ToolResult(text="ok", is_error=False, diff=("new.py", "", "one\ntwo\nthree"))
    assert result_digest("Write", r) == "+3"


def test_read_digest_counts_lines():
    r = ToolResult(text="\n".join(f"line{i}" for i in range(501)), is_error=False)
    assert result_digest("Read", r) == "501 lines"


def test_grep_digest_says_no_matches_when_empty():
    assert result_digest("Grep", ToolResult(text="", is_error=False)) == "no matches"


def test_grep_digest_counts_matches():
    r = ToolResult(text="a.py:1:x\nb.py:2:y", is_error=False)
    assert result_digest("Grep", r) == "2 matches"


def test_bash_digest_takes_the_last_non_empty_line():
    # The verdict of a command lives at the end: "3629 passed", not the
    # progress bar the first line carries.
    r = ToolResult(
        text="....... [ 1%]\n....... [99%]\n\n3629 passed\n\n", is_error=False
    )
    assert result_digest("Bash", r) == "3629 passed"


def test_unknown_tool_digest_takes_the_first_non_empty_line():
    r = ToolResult(text="\n\nmonitor_id: mon_4f2a\nmore\n", is_error=False)
    assert result_digest("mcp__aegis__aegis_monitor", r) == "monitor_id: mon_4f2a"


def test_error_digest_ignores_the_tool_shape():
    # An error result does not have the shape the success digest reads:
    # a failed Read is a message, not a line count.
    r = ToolResult(text="File does not exist: /nope.py\nand more", is_error=True)
    assert result_digest("Read", r) == "File does not exist: /nope.py"


def test_digest_is_empty_while_the_call_is_in_flight():
    assert result_digest("Bash", None) == ""


def test_digest_of_a_silent_result_is_ok():
    assert result_digest("Bash", ToolResult(text="   \n\n", is_error=False)) == "ok"


def test_digest_clips_a_long_line():
    # The row clips the digest to its own column; this bound only stops us
    # formatting a 2 MB result into a line nobody will read.
    r = ToolResult(text="x" * 5000, is_error=False)
    out = result_digest("Bash", r)
    assert len(out) <= 200 and out.endswith("…")


# --- the row's label carries no input ----------------------------------
# What a transcript row is for is the RESULT. The label says which call it
# was; the command, the replaced string and the search path are input, and
# they belong in the detail window (Alex, 2026-09-21: "why am i seeing so
# much the actual command when what i want is to see at a glance the
# result").

from aegis.render_shared import tool_label


def test_a_bash_label_is_the_description_not_the_command():
    lbl = tool_label(
        "Bash",
        {"description": "run the test suite", "command": "uv run pytest -q tests/"},
    )
    assert lbl == "run the test suite"
    assert "pytest" not in lbl


def test_a_bash_label_falls_back_to_the_command_when_undescribed():
    lbl = tool_label("Bash", {"command": "ls -la /tmp"})
    assert lbl == "ls -la /tmp"


def test_an_edit_label_names_the_file_not_the_replaced_text():
    lbl = tool_label(
        "Edit",
        {
            "file_path": "/a/b/pane.py",
            "old_string": "running = not track.done",
            "new_string": "running = False",
        },
    )
    assert lbl == "edit pane.py"
    assert "track.done" not in lbl


def test_a_grep_label_keeps_the_pattern_and_drops_the_path():
    # The pattern is which search this was; the path is where it looked.
    lbl = tool_label("Grep", {"pattern": "expanded", "path": "src/aegis/tui/pane.py"})
    assert lbl == "grep 'expanded'"


def test_a_read_label_is_the_file():
    assert tool_label("Read", {"file_path": "/a/b/render.py"}) == "read render.py"


def test_a_websearch_label_keeps_its_query():
    # For a search the query IS the identity of the call, not an argument
    # you would go looking for later.
    lbl = tool_label("WebSearch", {"query": "textual compositor render strips"})
    assert "textual compositor" in lbl
