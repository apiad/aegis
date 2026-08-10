"""render_repos — the REPOS rows. Pure, so cheap to pin exactly."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.repos.models import RepoState, RepoView
from aegis.repos.render import render_repos
from aegis.themes import AegisColors
from aegis.tui.fit import strip_markup

PALETTE = AegisColors(ready="green", working="yellow", error="red",
                      accent="cyan", muted="grey50", ok="green", err="red",
                      user="cyan", user_bg="black")

WIDE = 60
NARROW = 26


def view(name="aegis", *, branch="main", dirty=0, ahead=0, behind=0,
         detached=False, op="", stale=False, writers=("alice",),
         mine=True, host="local"):
    return RepoView(
        state=RepoState(root=Path("/w/repos") / name, branch=branch,
                        dirty=dirty, ahead=ahead, behind=behind,
                        detached=detached, op=op, stale=stale),
        writers=writers, mine=mine, host=host)


def text(views, width=WIDE):
    out = render_repos(views, PALETTE, width)
    return "" if out is None else out.plain


def lines(views, width=WIDE):
    return [ln for ln in text(views, width).split("\n") if ln.strip()]


# --- the empty case ---------------------------------------------------

def test_no_repos_renders_nothing_at_all():
    """Not a heading, not a blank row — the rule every section in this
    column follows."""
    assert render_repos([], PALETTE, WIDE) is None


# --- the row ----------------------------------------------------------

def test_a_row_carries_name_branch_and_counts():
    row = lines([view(dirty=7, ahead=2)])[0]
    assert "aegis" in row
    assert "main" in row
    assert "~7" in row
    assert "↑2" in row


def test_a_clean_repo_shows_no_counts():
    """Clean is quiet: zeros would make every row look like it had news."""
    row = lines([view()])[0]
    assert "~" not in row
    assert "↑" not in row and "↓" not in row


def test_behind_is_distinct_from_ahead():
    assert "↓3" in lines([view(behind=3)])[0]


def test_rows_keep_the_order_they_are_given():
    """The tracker has already sorted by recency; the renderer must not
    re-sort, or the most recent write stops leading."""
    got = lines([view("warden", writers=("bob",), mine=False),
                 view("aegis")])
    assert [ln.split()[1] for ln in got] == ["warden", "aegis"]


# --- the mark ---------------------------------------------------------

def test_my_repo_and_a_peers_repo_carry_different_marks():
    mine = lines([view(mine=True)])[0]
    theirs = lines([view(writers=("bob",), mine=False)])[0]
    assert mine.lstrip().startswith("●")
    assert theirs.lstrip().startswith("·")


def test_the_mark_is_space_separated_from_the_name():
    """● is East Asian Ambiguous — Rich measures one cell, terminals draw
    two, and a glued neighbour overlaps it. The plan dock pays this same
    tax on every circle it draws."""
    row = lines([view()])[0]
    assert row.lstrip()[1] == " "


def test_a_shared_repo_is_amber():
    out = render_repos([view(writers=("alice", "bob"))], PALETTE, WIDE)
    assert "yellow" in out.markup       # palette.working
    solo = render_repos([view(writers=("alice",))], PALETTE, WIDE)
    assert "yellow" not in solo.markup


# --- peers ------------------------------------------------------------

def test_a_wide_column_spells_the_peer_handles():
    row = lines([view(writers=("alice", "calm-hopper"))], WIDE)[0]
    assert "calm-hopper" in row
    assert "alice" not in row           # the mark already says "you"


def test_a_narrow_column_counts_the_peers_instead():
    # Counts included on purpose: without them a short name and one handle
    # genuinely fit in 26 cells, and the test would pass without the +N
    # tier ever being reached.
    row = lines([view(dirty=7, ahead=2,
                      writers=("alice", "calm-hopper"))], NARROW)[0]
    assert "calm-hopper" not in row
    assert "+1" in row


def test_a_peers_own_row_names_every_writer():
    row = lines([view(writers=("bob", "carol"), mine=False)], WIDE)[0]
    assert "bob" in row and "carol" in row


# --- degradation ------------------------------------------------------

def test_a_long_repo_name_truncates_rather_than_dropping_the_row():
    """fit_rows answers 'no tier fits' by omitting the segment. A repo that
    vanished because its name was long reads exactly like a repo nobody
    touched."""
    got = lines([view("a-very-long-repository-name-indeed", dirty=4)], NARROW)
    assert len(got) == 1
    assert "a-very" in got[0]


def test_no_row_exceeds_the_column():
    long_ = view("another-quite-long-repo", dirty=12, ahead=3, behind=4,
                 writers=("alice", "bob", "carol"))
    for width in (NARROW, 34, WIDE):
        for ln in text([long_], width).split("\n"):
            assert len(strip_markup(ln)) <= width, (width, ln)


def test_an_unmeasured_width_still_renders():
    """size.width is 0 before layout — a widget that was display:none has
    never been laid out."""
    assert "aegis" in text([view()], 0)


# --- states that override the branch ----------------------------------

def test_a_detached_head_says_so_where_the_branch_goes():
    row = lines([view(branch="", detached=True)])[0]
    assert "(detached)" in row


def test_an_operation_in_progress_replaces_the_branch():
    row = lines([view(branch="main", op="rebase")])[0]
    assert "(rebase)" in row


def test_detached_and_an_operation_render_in_the_error_colour():
    out = render_repos([view(branch="", detached=True)], PALETTE, WIDE)
    assert "red" in out.markup


# --- stale and remote -------------------------------------------------

def test_a_stale_row_still_shows_what_it_knows():
    row = lines([view(branch="main", dirty=7, stale=True)])[0]
    assert "main" in row
    assert "~7" in row


def test_a_stale_row_is_dimmed():
    stale = render_repos([view(stale=True)], PALETTE, WIDE).markup
    fresh = render_repos([view(stale=False)], PALETTE, WIDE).markup
    assert stale != fresh
    assert "grey50" in stale            # palette.muted


def test_an_off_host_row_is_host_qualified_and_carries_no_git_state():
    row = lines([view("warden", branch="", host="vps",
                      writers=("bob",), mine=False)])[0]
    assert "warden@vps" in row
    assert "main" not in row
    assert "~" not in row


@pytest.mark.parametrize("width", [NARROW, 30, 40, WIDE, 80])
def test_every_row_survives_every_width(width):
    """A row must never be dropped: the section is a list of repos, and a
    missing entry reads as 'nobody is in there'."""
    views = [view("aegis", dirty=7, ahead=2, writers=("alice", "bob")),
             view("Workspace", dirty=2),
             view("a-repository-with-a-truly-excessive-name", behind=1,
                  writers=("carol",), mine=False)]
    assert len(lines(views, width)) == 3
