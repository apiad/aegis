from __future__ import annotations

from aegis.hosts.models import Place
from aegis.render_shared import file_target


def test_local_read_still_yields_a_target():
    t = file_target("Read", {"file_path": "/home/apiad/Workspace/x.py"})
    assert t is not None
    assert t.path == "/home/apiad/Workspace/x.py"


def test_remote_read_yields_no_local_target():
    # The same path exists on both machines and is a DIFFERENT file.
    # Opening the local one would be a silent wrong answer, which is
    # worse than not opening anything.
    t = file_target("Read", {"file_path": "/home/apiad/Workspace/x.py"},
                    host="vps")
    assert t is None


def test_remote_edit_yields_no_local_target():
    assert file_target(
        "Edit", {"file_path": "/x.py", "old_string": "a", "new_string": "b"},
        host="vps") is None


def test_remote_write_yields_no_local_target():
    assert file_target("Write", {"file_path": "/x.py", "content": "hi"},
                       host="vps") is None


def test_explicit_local_host_behaves_like_the_default():
    a = file_target("Read", {"file_path": "/x.py"})
    b = file_target("Read", {"file_path": "/x.py"}, host="local")
    assert a is not None and b is not None
    assert a.path == b.path


def test_place_qualifies_a_path_for_display():
    assert Place("vps", "/w").qualify("/w/x.py") == "vps:/w/x.py"
    assert Place("local", "/w").qualify("/w/x.py") == "/w/x.py"


def test_place_knows_whether_it_is_local():
    assert Place("local", "/w").is_local
    assert not Place("vps", "/w").is_local


def test_a_remote_block_offers_the_qualified_path_instead_of_opening():
    """The pair that matters: no local target AND a qualified path to
    show. Either alone would be half the fix — no target with nothing
    offered is a dead gesture, a target off-host is the silent wrong
    file."""
    from aegis.events import ToolUse
    from aegis.tui.pane import ConversationPane

    ev = ToolUse(name="Read", summary="read x.py", tool_call_id="t1",
                 raw_input={"file_path": "/home/apiad/Workspace/x.py"})

    pane = ConversationPane.__new__(ConversationPane)
    pane._place = Place("vps", "/home/apiad/Workspace")
    assert pane._remote_path_for(ev) == "vps:/home/apiad/Workspace/x.py"
    assert file_target(ev.name, ev.raw_input, host=pane._host) is None

    pane._place = Place("local", "/home/apiad/Workspace")
    assert pane._remote_path_for(ev) is None
    assert file_target(ev.name, ev.raw_input, host=pane._host) is not None


def test_a_remote_block_without_a_file_path_offers_nothing():
    from aegis.events import ToolUse
    from aegis.tui.pane import ConversationPane

    pane = ConversationPane.__new__(ConversationPane)
    pane._place = Place("vps", "/w")
    ev = ToolUse(name="Bash", summary="ls", tool_call_id="t1",
                 raw_input={"command": "ls"})
    assert pane._remote_path_for(ev) is None


def test_a_remote_tab_is_marked_with_its_host():
    from aegis.tui.app import _tab_suffix

    class _Pane:
        handle = "a-b"

        class _core:
            place = Place("vps", "/w")

    assert _tab_suffix(_Pane(), None) == "@vps"


def test_a_local_tab_carries_no_host_marker():
    from aegis.tui.app import _tab_suffix

    class _Pane:
        handle = "a-b"

        class _core:
            place = Place("local", "/w")

    assert _tab_suffix(_Pane(), None) is None


def test_a_remote_worker_tab_shows_both_labels():
    from aegis.tui.app import _tab_suffix

    class _Pane:
        handle = "a-b"

        class _core:
            place = Place("vps", "/w")

    class _QM:
        def worker_label(self, handle):
            return "general#01ABC"

    assert _tab_suffix(_Pane(), _QM()) == "general#01ABC @vps"


# --- Ctrl+N host tier -----------------------------------------------------


def test_host_rows_put_local_first():
    from aegis.tui.picker import build_host_rows

    rows = build_host_rows(["vps", "smaug"], local_label="/home/apiad/Work")
    assert rows[0][0] == "local"
    assert "/home/apiad/Work" in rows[0][1]
    assert [v for v, _ in rows] == ["local", "smaug", "vps"]


def test_host_rows_with_no_configured_hosts_offer_only_local():
    from aegis.tui.picker import build_host_rows

    assert [v for v, _ in build_host_rows([], local_label="/x")] == ["local"]
