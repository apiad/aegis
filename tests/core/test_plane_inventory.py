"""Every plane the brain grows is classified, or this fails.

Two of every plane existed because adding one to the brain obliged nobody
to wire it to the views: `attach_monitor_manager` landed, the UI kept
rendering its own MonitorManager, and nothing anywhere said so. This test
is the thing that says so.

It does not check that a plane is adopted. It checks that somebody DECIDED,
which is the step that was missing.
"""
from __future__ import annotations

from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.core.planes import BRAIN_PLANES, CONSTRUCTED_PLANES, NOT_VIEW_FACING


def _attach_methods() -> set[str]:
    return {name for name in dir(SessionManager)
            if name.startswith("attach_")}


def test_every_attach_is_classified():
    """A new `attach_foo` must be named in one list or the other. Erring
    toward the loud side: the cost of classifying is one line, the cost of
    forgetting is a UI that lies about what is running."""
    classified = set(NOT_VIEW_FACING) | {f"attach_{p}" for p in BRAIN_PLANES}
    unclassified = _attach_methods() - classified
    assert not unclassified, (
        f"unclassified planes: {sorted(unclassified)}. Add each to "
        "BRAIN_PLANES (a view renders it) or to NOT_VIEW_FACING (with the "
        "reason it does not).")


def test_nothing_is_in_both_lists():
    both = {f"attach_{p}" for p in BRAIN_PLANES} & set(NOT_VIEW_FACING)
    assert not both, f"claimed twice: {sorted(both)}"


def test_every_brain_plane_has_its_attach():
    """The adoption code reads attribute N for `attach_N`; a typo in the
    inventory would otherwise surface as a view raising at construction."""
    for plane in BRAIN_PLANES:
        assert f"attach_{plane}" in _attach_methods(), (
            f"{plane!r} has no attach_{plane} on SessionManager")


def test_every_constructed_plane_exists_on_a_fresh_manager(tmp_path):
    """These have no `attach_*`, so only a real manager can say whether the
    name is right."""
    mgr = SessionManager({}, "", make_session=None, mcp=None,
                         roots=AegisRoots.for_project(tmp_path))
    for plane in CONSTRUCTED_PLANES:
        assert hasattr(mgr, plane), f"SessionManager has no {plane!r}"
