"""A member sees only its own on a real multi-user server (#566; the spec's
section 08). Run with ``make test-multiuser``."""

from __future__ import annotations

import json

import pytest

from .conftest import MEMBERS
from .test_switch import spawn

pytestmark = [pytest.mark.slow, pytest.mark.multiuser]

BEN, CARA = MEMBERS


async def test_a_member_lists_and_reaches_only_its_own(mu_server):
    s = mu_server
    mine, _ = await spawn(s, BEN)
    theirs, _ = await spawn(s, CARA)
    rows = (await s.watch("admin", "sessions", lambda m: theirs in json.dumps(m)))[0]
    handle = {r["log_id"]: r["handle"] for r in rows["data"]}
    listed = [x["handle"] for x in await s.call(BEN, "session.list", {})]
    assert handle[mine] in listed and handle[theirs] not in listed
    every = [x["handle"] for x in await s.call("admin", "session.list", {})]
    assert handle[theirs] in every
    snap = await s.watch(BEN, "sessions", lambda m: mine in json.dumps(m))
    assert theirs not in json.dumps(snap)
    with pytest.raises(RuntimeError, match="no_session"):
        await s.call(BEN, "session.read", {"log_id": theirs, "ids": []})
    (msg,) = await s.watch(BEN, f"transcript:{theirs}", lambda m: True, timeout=5)
    assert msg["t"] == "error" and msg["error"]["code"] == "unknown_channel"
