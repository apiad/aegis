from __future__ import annotations

import pytest

from aegis.groups.models import MemberRef
from aegis.groups.registry import GroupExists, GroupRegistry, UnknownGroup


def test_create_then_get():
    reg = GroupRegistry()
    g = reg.create("reviewers")
    assert g.name == "reviewers"
    assert reg.get("reviewers") is g


def test_create_rejects_duplicate_live_name():
    reg = GroupRegistry()
    reg.create("reviewers")
    with pytest.raises(GroupExists):
        reg.create("reviewers")


def test_add_member_creates_group_implicitly():
    reg = GroupRegistry()
    reg.add_member("auditors", MemberRef(handle="ada", profile="sec"))
    assert "ada" in reg.get("auditors").members


def test_remove_last_member_auto_closes_group():
    reg = GroupRegistry()
    reg.add_member("auditors", MemberRef(handle="ada", profile="sec"))
    reg.remove_member("auditors", "ada")
    with pytest.raises(UnknownGroup):
        reg.get("auditors")


def test_dissolve_removes_group_even_with_members():
    reg = GroupRegistry()
    reg.add_member("auditors", MemberRef(handle="ada", profile="sec"))
    reg.add_member("auditors", MemberRef(handle="lucid", profile="logic"))
    reg.dissolve("auditors")
    with pytest.raises(UnknownGroup):
        reg.get("auditors")


def test_rename_moves_under_new_key_and_frees_old():
    reg = GroupRegistry()
    reg.add_member("auditors", MemberRef(handle="ada", profile="sec"))
    reg.rename("auditors", "reviewers")
    assert reg.get("reviewers").name == "reviewers"
    with pytest.raises(UnknownGroup):
        reg.get("auditors")


def test_rename_rejects_collision_with_live_name():
    reg = GroupRegistry()
    reg.add_member("a", MemberRef(handle="x", profile="p"))
    reg.add_member("b", MemberRef(handle="y", profile="p"))
    with pytest.raises(GroupExists):
        reg.rename("a", "b")


def test_move_member_between_groups():
    reg = GroupRegistry()
    reg.add_member("a", MemberRef(handle="x", profile="p"))
    reg.add_member("b", MemberRef(handle="y", profile="p"))
    reg.move_member("x", from_group="a", to_group="b")
    assert "x" in reg.get("b").members
    with pytest.raises(UnknownGroup):
        reg.get("a")
