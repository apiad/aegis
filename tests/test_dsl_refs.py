from __future__ import annotations

import pytest

from aegis.dsl.refs import RefError, Store, resolve_selector, substitute


def _store():
    s = Store()
    s.record("root.0", "list", {"files": ["a.ts", "b.ts"]})
    s.record("root.1", "rounds", [{"n": 1}, {"n": 2}])
    return s


def test_resolve_whole_output():
    assert resolve_selector("list", _store()) == {"files": ["a.ts", "b.ts"]}


def test_resolve_dotted_path():
    assert resolve_selector("list.files", _store()) == ["a.ts", "b.ts"]


def test_resolve_list_index():
    assert resolve_selector("list.files.0", _store()) == "a.ts"


def test_resolve_loop_last_sentinel():
    assert resolve_selector("rounds.last", _store()) == {"n": 2}


def test_resolve_missing_id_raises():
    with pytest.raises(RefError):
        resolve_selector("nope.x", _store())


def test_substitute_binds_names():
    assert substitute("Audit {{item}} now", {"item": "a.ts"}) == "Audit a.ts now"


def test_substitute_unbound_raises():
    with pytest.raises(RefError):
        substitute("Hi {{missing}}", {})


def test_substitute_no_logic_in_braces():
    # Only bare names resolve; anything else is an unbound-name error.
    with pytest.raises(RefError):
        substitute("{{a.b + 1}}", {"a": 1})
