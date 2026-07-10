from aegis.locks.persistence import PersistedClaimLog
from aegis.locks.registry import ClaimRegistry


def test_claim_then_reload_rebuilds_live_set(tmp_path):
    log = PersistedClaimLog(tmp_path)
    r = ClaimRegistry(live_handles=lambda: {"a", "b"}, log=log)
    r.claim("a", ["src/x/"], [], intent="exclusive")
    c2, _, _ = r.claim("b", ["src/y/"], [], intent="shared")
    r.release(c2.claim_id, "b")

    # fresh registry over the same log
    log2 = PersistedClaimLog(tmp_path)
    r2 = ClaimRegistry(live_handles=lambda: {"a", "b"}, log=log2)
    r2.start()
    handles = {c.handle for c in r2.active()}
    assert handles == {"a"}          # a's claim survives, b's was released


def test_replay_reaps_dead_holder(tmp_path):
    log = PersistedClaimLog(tmp_path)
    r = ClaimRegistry(live_handles=lambda: {"a"}, log=log)
    r.claim("a", ["src/x/"], [], intent="exclusive")
    r.claim("a", ["src/z/"], [], intent="exclusive")

    log2 = PersistedClaimLog(tmp_path)
    # on reboot, "a" is no longer live
    r2 = ClaimRegistry(live_handles=lambda: set(), log=log2)
    r2.start()
    assert r2.active() == []


def test_torn_trailing_line_tolerated(tmp_path):
    log = PersistedClaimLog(tmp_path)
    r = ClaimRegistry(live_handles=lambda: {"a"}, log=log)
    r.claim("a", ["src/x/"], [], intent="exclusive")
    # append a torn line
    p = log.path()
    with p.open("a") as f:
        f.write('{"kind": "claimed", "claim_id": "trunc')
    log2 = PersistedClaimLog(tmp_path)
    r2 = ClaimRegistry(live_handles=lambda: {"a"}, log=log2)
    r2.start()                       # must not raise
    assert len(r2.active()) == 1
