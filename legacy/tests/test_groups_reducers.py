from __future__ import annotations

import pytest

from aegis.groups.models import MemberResult
from aegis.groups.reducers import concat, get_reducer


def _mr(handle: str, text: str) -> MemberResult:
    return MemberResult(handle=handle, text=text, turn_ms=0,
                        tokens_in=0, tokens_out=0, status="done")


def test_concat_joins_with_handle_headers_in_completion_order():
    by_member = {"a": _mr("a", "hello"), "b": _mr("b", "world")}
    out = concat(by_member, order=["b", "a"])
    assert out == "---\nb: world\n\n---\na: hello"


def test_get_reducer_returns_concat_for_concat_name():
    assert get_reducer("concat") is concat


def test_get_reducer_raises_on_unknown():
    with pytest.raises(KeyError):
        get_reducer("does-not-exist")


def test_join_by_handle_returns_dict():
    by_member = {"a": _mr("a", "x"), "b": _mr("b", "y")}
    out = get_reducer("join_by_handle")(by_member, ["a", "b"])
    assert out == {"a": "x", "b": "y"}


def test_last_wins_returns_text_of_last_finisher():
    by_member = {"a": _mr("a", "first"), "b": _mr("b", "second")}
    out = get_reducer("last_wins")(by_member, ["a", "b"])
    assert out == "second"


def test_majority_vote_returns_modal_with_tiebreak_first_finisher():
    by_member = {
        "a": _mr("a", "YES"), "b": _mr("b", "NO"), "c": _mr("c", "YES"),
    }
    out = get_reducer("majority_vote")(by_member, ["a", "b", "c"])
    assert out == "YES"
