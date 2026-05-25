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
