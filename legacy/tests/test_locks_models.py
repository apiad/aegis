from aegis.locks.models import Claim, claims_overlap


def _claim(prefixes=(), files=(), handle="a", intent="shared"):
    return Claim(claim_id="c-" + handle, handle=handle,
                 prefixes=frozenset(prefixes), files=frozenset(files),
                 intent=intent, desc="", since="2026-07-10T00:00:00Z")


def test_file_file_intersection_overlaps():
    a = _claim(files=["src/x.py"])
    b = _claim(files=["src/x.py"], handle="b")
    assert claims_overlap(a, b) is True


def test_disjoint_files_do_not_overlap():
    a = _claim(files=["src/x.py"])
    b = _claim(files=["src/y.py"], handle="b")
    assert claims_overlap(a, b) is False


def test_file_under_prefix_overlaps():
    a = _claim(prefixes=["src/aegis/tui/"])
    b = _claim(files=["src/aegis/tui/app.py"], handle="b")
    assert claims_overlap(a, b) is True


def test_prefix_under_prefix_overlaps():
    a = _claim(prefixes=["src/aegis/"])
    b = _claim(prefixes=["src/aegis/tui/"], handle="b")
    assert claims_overlap(a, b) is True


def test_sibling_prefixes_do_not_overlap():
    a = _claim(prefixes=["src/aegis/tui/"])
    b = _claim(prefixes=["src/aegis/mcp/"], handle="b")
    assert claims_overlap(a, b) is False


def test_prefix_boundary_is_slash_safe():
    # "src/aegisx/" must NOT be considered under "src/aegis/"
    a = _claim(prefixes=["src/aegis/"])
    b = _claim(prefixes=["src/aegisx/"], handle="b")
    assert claims_overlap(a, b) is False
