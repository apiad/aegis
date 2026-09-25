"""reconnect refuses local sessions because the manual /reconnect command
is documented as a remote-link repair. The MECHANISM is provider-level
resume_from, which is how every boot resume works, locally included. The
recovery plane needs it for local workers, so the guard becomes a
parameter rather than a property of the code.

The manager-with-one-local-session rig lives in `tests/core/conftest.py`;
`test_recovery_rebuild.py` drives the same two fixtures.
"""
from __future__ import annotations

import pytest


async def test_local_session_is_refused_by_default(brain_with_local_session):
    mgr, handle = brain_with_local_session
    with pytest.raises(ValueError, match="reconnect is for remote sessions"):
        await mgr.reconnect(handle)


async def test_local_session_is_allowed_when_asked(brain_with_local_session):
    mgr, handle = brain_with_local_session
    result = await mgr.reconnect(handle, allow_local=True)
    assert handle in result


async def test_a_session_with_no_id_is_refused_even_with_allow_local(
    brain_with_local_session_no_id,
):
    """allow_local lifts one guard, not both. Resuming on no id builds a
    fresh conversation and calls it a recovery."""
    mgr, handle = brain_with_local_session_no_id
    with pytest.raises(ValueError, match="no session id"):
        await mgr.reconnect(handle, allow_local=True)
