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
