from aegis.locks.registry import ClaimRegistry


def _reg(live=("a", "b", "c")):
    live_set = set(live)
    return ClaimRegistry(live_handles=lambda: set(live_set))


def test_shared_over_shared_both_granted_and_surfaced():
    r = _reg()
    c1, g1, o1 = r.claim("a", ["src/x/"], [], intent="shared")
    assert g1 is True and o1 == []
    c2, g2, o2 = r.claim("b", ["src/x/"], [], intent="shared")
    assert g2 is True
    assert [c.handle for c in o2] == ["a"]      # sees the peer
    assert len(r.active()) == 2


def test_exclusive_over_existing_shared_denied():
    r = _reg()
    r.claim("a", ["src/x/"], [], intent="shared")
    c2, g2, o2 = r.claim("b", ["src/x/"], [], intent="exclusive")
    assert g2 is False
    assert [c.handle for c in o2] == ["a"]
    # denied claim was NOT recorded
    assert [c.handle for c in r.active()] == ["a"]


def test_shared_over_existing_exclusive_denied():
    r = _reg()
    r.claim("a", ["src/x/"], [], intent="exclusive")
    c2, g2, o2 = r.claim("b", ["src/x/"], [], intent="shared")
    assert g2 is False
    assert [c.handle for c in o2] == ["a"]


def test_exclusive_over_empty_granted():
    r = _reg()
    c1, g1, o1 = r.claim("a", [], ["src/x.py"], intent="exclusive")
    assert g1 is True and o1 == []


def test_release_is_idempotent_and_ownership_scoped():
    r = _reg()
    c1, _, _ = r.claim("a", ["src/x/"], [])
    assert r.release(c1.claim_id, "b") is False   # not the owner
    assert r.release(c1.claim_id, "a") is True
    assert r.release(c1.claim_id, "a") is False   # already gone
    assert r.active() == []


def test_dead_holder_claim_is_reaped_from_active():
    live = {"a"}
    r = ClaimRegistry(live_handles=lambda: set(live))
    r.claim("a", ["src/x/"], [], intent="exclusive")
    r.claim("gone", ["src/y/"], [], intent="exclusive")  # holder not live
    handles = {c.handle for c in r.active()}
    assert handles == {"a"}        # "gone" filtered out
    # and a new exclusive claim over src/y/ now succeeds
    _, g, _ = r.claim("a", ["src/y/"], [], intent="exclusive")
    assert g is True
