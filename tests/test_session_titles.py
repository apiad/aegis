"""Title precedence and sanitizing.

Precedence is the whole concurrency story for titles, so it gets one test
per ordered pair rather than a spot check: the interesting failures are
the ones where a *lower* authority silently wins.
"""
from __future__ import annotations

import pytest

from aegis.state.titles import TITLE_RANK, outranks, sanitize_title


@pytest.mark.parametrize("new,current,allowed", [
    # auto beats only "unset"
    ("auto", "", True),
    ("auto", "auto", True),
    ("auto", "agent", False),
    ("auto", "human", False),
    # agent beats auto and unset, not human
    ("agent", "", True),
    ("agent", "auto", True),
    ("agent", "agent", True),
    ("agent", "human", False),
    # human beats everything, including itself (retyping is not a conflict)
    ("human", "", True),
    ("human", "auto", True),
    ("human", "agent", True),
    ("human", "human", True),
])
def test_precedence_is_human_over_agent_over_auto(new, current, allowed):
    assert outranks(new, current) is allowed


def test_unknown_sources_rank_lowest():
    assert TITLE_RANK.get("nonsense", 0) == 0
    assert outranks("nonsense", "auto") is False
    assert outranks("auto", "nonsense") is True


@pytest.mark.parametrize("raw,expected", [
    ("fix the eviction race", "fix the eviction race"),
    ("  padded  ", "padded"),
    ("first line\nsecond line", "first line"),
    ('"quoted"', "quoted"),
    ("`backticked`", "backticked"),
    ("'single'", "single"),
    ("“curly”", "curly"),
    ("collapse    inner   space", "collapse inner space"),
    ("trailing punctuation.", "trailing punctuation"),
    ("", ""),
    ("   ", ""),
    ("\n\n", ""),
])
def test_sanitizer_table(raw, expected):
    assert sanitize_title(raw) == expected


def test_sanitizer_truncates_on_a_word_boundary_with_an_ellipsis():
    out = sanitize_title("alpha beta gamma delta epsilon zeta", cap=20)
    assert len(out) <= 20
    assert out.endswith("…")
    # cut at a space, so no half-word is left before the ellipsis
    assert out == "alpha beta gamma…"


def test_sanitizer_truncates_a_single_long_word_hard():
    out = sanitize_title("x" * 100, cap=10)
    assert out == "x" * 9 + "…"
    assert len(out) == 10


def test_sanitizer_default_cap_is_tab_sized():
    out = sanitize_title("a" * 200)
    assert len(out) <= 32


# --- the write path -------------------------------------------------
#
# set_title is where precedence stops being a pure function and becomes a
# rule the substrate enforces, so these drive a real SessionManager rather
# than asserting on outranks() a second time.

class FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


@pytest.fixture
def manager_with_session():
    """A SessionManager with one live session; yields (manager, handle)."""
    from aegis.core.manager import SessionManager
    mgr = SessionManager(
        {"default": object()}, "default",
        make_session=lambda profile, url, handle: FakeHarness(),
        mcp=None, inbox=None)
    session = mgr._sync_spawn("default")
    return mgr, session.handle


@pytest.mark.asyncio
async def test_manager_set_title_records_source(manager_with_session):
    mgr, handle = manager_with_session
    res = await mgr.set_title(handle, "eviction race", source="human")
    assert res["ok"] is True
    assert mgr.get(handle).title == "eviction race"
    assert mgr.get(handle).title_source == "human"


@pytest.mark.asyncio
async def test_a_session_starts_with_no_title(manager_with_session):
    mgr, handle = manager_with_session
    assert mgr.get(handle).title == ""
    assert mgr.get(handle).title_source == ""


@pytest.mark.asyncio
async def test_an_agent_cannot_overwrite_a_human_title(manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, "operator wrote this", source="human")
    res = await mgr.set_title(handle, "agent wrote this", source="agent")
    assert "error" in res
    # The refusal says why, rather than failing silently.
    assert "human" in res["error"]
    assert mgr.get(handle).title == "operator wrote this"


@pytest.mark.asyncio
async def test_a_human_overwrites_an_agent_title(manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, "agent wrote this", source="agent")
    res = await mgr.set_title(handle, "operator wrote this", source="human")
    assert res["ok"] is True
    assert mgr.get(handle).title == "operator wrote this"


@pytest.mark.asyncio
async def test_set_title_sanitizes_before_storing(manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, '  "wrapped\nand long"  ', source="human")
    stored = mgr.get(handle).title
    assert stored == sanitize_title('  "wrapped\nand long"  ')
    assert "\n" not in stored
    assert not stored.startswith('"')


@pytest.mark.asyncio
async def test_empty_title_clears_and_resets_the_source(manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, "something", source="human")
    res = await mgr.set_title(handle, "", source="human")
    assert res["ok"] is True
    assert mgr.get(handle).title == ""
    # Source resets too, or nothing could ever set a title again.
    assert mgr.get(handle).title_source == ""


@pytest.mark.asyncio
async def test_set_title_on_an_unknown_handle_errors(manager_with_session):
    mgr, _ = manager_with_session
    res = await mgr.set_title("no-such-agent", "x", source="human")
    assert "error" in res


@pytest.mark.asyncio
async def test_rename_preserves_the_title(manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, "eviction race", source="human")
    await mgr.rename_handle(handle, "fix-eviction")
    assert mgr.get("fix-eviction").title == "eviction race"
    assert mgr.get("fix-eviction").title_source == "human"


@pytest.mark.asyncio
async def test_rename_can_set_a_title_in_one_call(manager_with_session):
    mgr, handle = manager_with_session
    res = await mgr.rename_handle(handle, "fix-eviction",
                                  title="eviction race")
    assert res["ok"] is True
    assert mgr.get("fix-eviction").title == "eviction race"
    assert mgr.get("fix-eviction").title_source == "agent"


@pytest.mark.asyncio
async def test_rename_title_does_not_override_a_human_one(
        manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, "operator wrote this", source="human")
    res = await mgr.rename_handle(handle, "fix-eviction",
                                  title="agent wrote this")
    # The rename still succeeds; only the title write is declined.
    assert res["ok"] is True
    assert mgr.get("fix-eviction").title == "operator wrote this"
