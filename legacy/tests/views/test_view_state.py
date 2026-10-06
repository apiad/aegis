from aegis.views.state import ViewState, load_view, save_view


def test_roundtrip(tmp_path):
    vs = ViewState(view_id="tty-1", geometry=(140, 50),
                   active_handle="lucid-knuth",
                   scroll={"lucid-knuth": 42}, drafts={"lucid-knuth": "half "})
    save_view(tmp_path, vs)
    assert load_view(tmp_path, "tty-1") == vs


def test_missing_view_is_none_not_an_error(tmp_path):
    """A first attach has no prior state; that is the normal case, not a
    failure."""
    assert load_view(tmp_path, "never-seen") is None


def test_two_views_do_not_share_a_file(tmp_path):
    save_view(tmp_path, ViewState("a", (80, 24), None, {}, {"h": "draft-a"}))
    save_view(tmp_path, ViewState("b", (140, 50), None, {}, {"h": "draft-b"}))
    assert load_view(tmp_path, "a").drafts == {"h": "draft-a"}
    assert load_view(tmp_path, "b").drafts == {"h": "draft-b"}


def test_lands_under_the_views_subdir(tmp_path):
    save_view(tmp_path, ViewState("tty-1", (80, 24), None, {}, {}))
    assert (tmp_path / "views" / "tty-1.json").is_file()


def test_a_damaged_view_file_is_none_not_a_raise(tmp_path):
    """A corrupt view file must cost that view its scroll position, never
    the daemon. Same posture as state/session_log.py's scan_log."""
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "tty-1.json").write_text("{not json", encoding="utf-8")
    assert load_view(tmp_path, "tty-1") is None


def test_a_view_id_cannot_escape_the_views_dir(tmp_path):
    """View ids arrive from clients in stage 5. A path separator in one must
    not write outside the state dir."""
    import pytest
    with pytest.raises(ValueError):
        save_view(tmp_path, ViewState("../../etc/passwd", (80, 24), None, {}, {}))
