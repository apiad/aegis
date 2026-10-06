"""The file a tool call points at — the pure half of ctrl+click-to-open."""
from __future__ import annotations

from aegis.render_shared import anchor_line, file_target


def test_read_carries_its_offset_as_the_line():
    t = file_target("Read", {"file_path": "/w/x.py", "offset": 42})
    assert t is not None
    assert (t.path, t.line, t.anchor) == ("/w/x.py", 42, None)


def test_read_without_offset_has_no_line():
    t = file_target("Read", {"file_path": "/w/x.py"})
    assert t is not None
    assert (t.path, t.line) == ("/w/x.py", None)


def test_write_targets_the_file_head():
    t = file_target("Write", {"file_path": "/w/new.py", "content": "x = 1"})
    assert t is not None
    assert (t.path, t.line, t.anchor) == ("/w/new.py", None, None)


def test_edit_carries_old_string_as_the_anchor():
    t = file_target("Edit", {"file_path": "/w/x.py",
                             "old_string": "def foo():", "new_string": "..."})
    assert t is not None
    assert (t.path, t.line, t.anchor) == ("/w/x.py", None, "def foo():")


def test_a_command_with_no_file_has_no_target():
    assert file_target("Bash", {"command": "ls"}) is None
    assert file_target("Grep", {"pattern": "x", "path": "src/"}) is None
    assert file_target("Read", {}) is None
    assert file_target("Read", None) is None


def test_locations_back_the_acp_harnesses_that_send_no_file_path():
    # gemini / opencode / lovelaice report a tool call's file through the
    # ACP `locations` field rather than a claude-shaped `file_path`.
    t = file_target("read_file", {"absolute_path": "/w/x.py"},
                    locations=(("/w/x.py", 12),))
    assert t is not None
    assert (t.path, t.line) == ("/w/x.py", 12)


def test_file_path_wins_over_a_stale_location():
    t = file_target("Read", {"file_path": "/w/x.py", "offset": 5},
                    locations=(("/w/other.py", 99),))
    assert t is not None
    assert (t.path, t.line) == ("/w/x.py", 5)


# --- anchor resolution ---------------------------------------------------

_SRC = "alpha\nbeta\ngamma\ndelta\n"


def test_anchor_line_is_one_based():
    assert anchor_line(_SRC, "alpha") == 1
    assert anchor_line(_SRC, "gamma") == 3


def test_anchor_line_matches_a_multi_line_anchor_at_its_first_line():
    assert anchor_line(_SRC, "beta\ngamma") == 2


def test_anchor_line_falls_back_to_the_anchors_first_line():
    # The file moved on since the edit: the full old_string no longer
    # matches, but its opening line still locates the neighbourhood.
    assert anchor_line("alpha\nbeta CHANGED\ngamma\n", "beta\ngamma") == 2


def test_anchor_line_gives_up_rather_than_guessing():
    assert anchor_line(_SRC, "nowhere") is None
    assert anchor_line(_SRC, "") is None
    assert anchor_line("", "alpha") is None
