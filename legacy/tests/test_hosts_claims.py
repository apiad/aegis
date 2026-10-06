from __future__ import annotations
import pytest

from aegis.locks.models import Claim, claims_overlap
from aegis.locks.registry import ClaimRegistry


def _claim(handle, files=(), prefixes=(), intent="shared", host="local"):
    return Claim(claim_id=f"c-{handle}-{host}", handle=handle,
                 prefixes=frozenset(prefixes), files=frozenset(files),
                 intent=intent, desc="", since="2026-08-04T00:00:00Z",
                 host=host)


def test_same_path_on_different_hosts_does_not_overlap():
    a = _claim("one", files=["src/foo.py"], host="local")
    b = _claim("two", files=["src/foo.py"], host="vps")
    assert not claims_overlap(a, b)


def test_same_path_on_the_same_host_still_overlaps():
    a = _claim("one", files=["src/foo.py"], host="vps")
    b = _claim("two", files=["src/foo.py"], host="vps")
    assert claims_overlap(a, b)


def test_prefix_containment_is_host_scoped():
    a = _claim("one", prefixes=["src/aegis/"], host="local")
    b = _claim("two", files=["src/aegis/tui/app.py"], host="vps")
    assert not claims_overlap(a, b)
    c = _claim("two", files=["src/aegis/tui/app.py"], host="local")
    assert claims_overlap(a, c)


def test_host_defaults_to_local():
    assert Claim(claim_id="x", handle="h", prefixes=frozenset(),
                 files=frozenset(["a"]), intent="shared", desc="",
                 since="2026-08-04T00:00:00Z").host == "local"


def _registry(*handles):
    # Claims are reaped when their holder is not a live session, so the
    # live-handle filter has to know about the agents under test.
    return ClaimRegistry(live_handles=lambda: set(handles))


def test_exclusive_on_another_host_does_not_block():
    reg = _registry("remote-one", "local-one")
    reg.claim("remote-one", prefixes=[], files=["src/foo.py"],
              intent="exclusive", host="vps")
    _c, granted, overlaps = reg.claim(
        "local-one", prefixes=[], files=["src/foo.py"],
        intent="exclusive", host="local")
    assert granted
    assert overlaps == []


def test_exclusive_on_the_same_host_still_blocks():
    reg = _registry("one", "two")
    reg.claim("one", prefixes=[], files=["src/foo.py"],
              intent="exclusive", host="vps")
    _c, granted, overlaps = reg.claim(
        "two", prefixes=[], files=["src/foo.py"],
        intent="exclusive", host="vps")
    assert not granted
    assert [o.handle for o in overlaps] == ["one"]


def test_claim_host_defaults_to_local_in_the_registry():
    c, granted, _ = _registry("one").claim(
        "one", prefixes=[], files=["a.py"])
    assert granted
    assert c.host == "local"


def test_a_persisted_claim_round_trips_its_host(tmp_path):
    from aegis.locks.persistence import PersistedClaimLog

    log = PersistedClaimLog(tmp_path)
    log.write(log.claimed(_claim("one", files=["src/foo.py"], host="vps")))
    replayed = PersistedClaimLog(tmp_path).replay()
    assert [c.host for c in replayed.values()] == ["vps"]


@pytest.mark.asyncio
async def test_aegis_claim_derives_the_host_from_the_calling_session(tmp_path):
    """The MCP tool must not take the caller's word for where it is — it
    looks the handle up in the live session list."""
    from aegis.locks.bridge import make_locks_bridge
    from aegis.mcp.bridge import SessionInfo
    from aegis.mcp.server import build_server
    from tests.test_mcp_server import FakeBridge, _call

    br = FakeBridge()
    live = {"remote-agent", "remote-two", "local-agent"}
    br.locks = make_locks_bridge(live_handles=lambda: set(live),
                                 root_fn=lambda: tmp_path)
    br.list_sessions = lambda: [
        SessionInfo(handle="remote-agent", agent_slug="main", state="ready",
                    active=False, unseen=False, host="vps"),
        SessionInfo(handle="remote-two", agent_slug="main", state="ready",
                    active=False, unseen=False, host="vps"),
        SessionInfo(handle="local-agent", agent_slug="main", state="ready",
                    active=False, unseen=False),
    ]
    srv = build_server(br)

    remote = await _call(srv, "aegis_claim", paths=["src/foo.py"],
                         from_handle="remote-agent", intent="exclusive")
    assert remote["granted"] is True and remote["host"] == "vps"

    # The identically-named path on the local machine is a different file,
    # so the exclusive claim above must not block it.
    local = await _call(srv, "aegis_claim", paths=["src/foo.py"],
                        from_handle="local-agent", intent="exclusive")
    assert local["granted"] is True and local["host"] == "local"

    # ...but a DIFFERENT agent on the same host still collides, so the
    # host gate has not simply disabled overlap detection.
    dup = await _call(srv, "aegis_claim", paths=["src/foo.py"],
                      from_handle="remote-two", intent="exclusive")
    assert dup["granted"] is False
    assert [o["handle"] for o in dup["overlaps"]] == ["remote-agent"]
    assert dup["overlaps"][0]["host"] == "vps"


def test_a_legacy_record_without_a_host_replays_as_local(tmp_path):
    # Pre-hosts JSONL logs must keep replaying — a missing host means the
    # claim was made before hosts existed, which is to say: local.
    import json

    from aegis.locks.persistence import PersistedClaimLog

    log = PersistedClaimLog(tmp_path)
    log.path().write_text(json.dumps({
        "kind": "claimed", "claim_id": "old-1", "handle": "one",
        "prefixes": [], "files": ["src/foo.py"], "intent": "shared",
        "desc": "", "since": "2026-07-01T00:00:00Z",
    }) + "\n")
    replayed = PersistedClaimLog(tmp_path).replay()
    assert [c.host for c in replayed.values()] == ["local"]
