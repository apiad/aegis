from aegis.locks.bridge import make_locks_bridge


def test_bridge_resolves_and_applies_grant_rule(tmp_path):
    live = {"a", "b"}
    br = make_locks_bridge(live_handles=lambda: set(live),
                           root_fn=lambda: tmp_path)
    c1, g1, o1 = br.claim("a", ["src/tui/"], intent="exclusive")
    assert g1 is True
    c2, g2, o2 = br.claim("b", ["src/tui/app.py"], intent="shared")
    assert g2 is False                     # under a's exclusive prefix
    assert [c.handle for c in o2] == ["a"]
    assert br.release(c1.claim_id, "a") is True
    c3, g3, _ = br.claim("b", ["src/tui/app.py"], intent="shared")
    assert g3 is True                      # a released; now clear


def test_bridge_persists_when_state_dir_given(tmp_path):
    br = make_locks_bridge(live_handles=lambda: {"a"},
                           root_fn=lambda: tmp_path,
                           state_dir=tmp_path)
    br.claim("a", ["src/x/"], intent="exclusive")
    assert (tmp_path / "locks" / "claims.jsonl").is_file()
