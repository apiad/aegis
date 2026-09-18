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
    r = ToolResult(text="x" * 200, is_error=False)
    out = result_digest("Bash", r)
    assert len(out) <= 60 and out.endswith("…")
