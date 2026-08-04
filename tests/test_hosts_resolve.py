from __future__ import annotations

import pytest

from aegis.hosts.errors import HostError
from aegis.hosts.models import HostSpec, Place
from aegis.hosts.resolve import resolve_place

HOSTS = {
    "vps": HostSpec(name="vps", ssh="vps.apiad.net",
                    cwd="/home/apiad/Workspace"),
}


def r(**kw):
    base = dict(host=None, cwd=None, agent_host=None,
                hosts=HOSTS, local_root="/local/proj")
    base.update(kw)
    return resolve_place(**base)


def test_defaults_to_local_at_the_project_root():
    assert r() == Place("local", "/local/proj")


def test_explicit_host_uses_that_hosts_cwd():
    assert r(host="vps") == Place("vps", "/home/apiad/Workspace")


def test_explicit_host_beats_the_profile_default():
    assert r(host="local", agent_host="vps") == Place("local", "/local/proj")


def test_profile_default_applies_when_no_explicit_host():
    assert r(agent_host="vps") == Place("vps", "/home/apiad/Workspace")


def test_explicit_cwd_beats_the_host_cwd():
    assert r(host="vps", cwd="/other/tree") == Place("vps", "/other/tree")


def test_explicit_cwd_applies_locally_too():
    assert r(cwd="/somewhere/else") == Place("local", "/somewhere/else")


def test_unknown_host_is_a_loud_error():
    with pytest.raises(HostError, match="nowhere"):
        r(host="nowhere")


def test_unknown_host_error_lists_the_known_ones():
    with pytest.raises(HostError, match="vps"):
        r(host="nowhere")
