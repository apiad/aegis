import json
import os
import stat

import pytest

from aegis.ops import OpError, Principal
from aegis.tokens import TokenStore


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path, owner="ana")


def test_the_admin_token_is_the_owners(store, tmp_path):
    token = (tmp_path / "token").read_text().strip()
    assert store.resolve(token) == Principal("ana", "admin")
    assert store.resolve("nope") is None


def test_an_added_token_resolves_and_only_its_digest_is_stored(store, tmp_path):
    t = store.add("ben", "member")
    assert store.resolve(t) == Principal("ben", "member")
    raw = (tmp_path / "tokens.json").read_text()
    assert t not in raw and "ben" in raw
    assert stat.S_IMODE(os.stat(tmp_path / "tokens.json").st_mode) == 0o600


@pytest.mark.parametrize(
    "name,role,code",
    [
        ("Ben", "member", "bad_name"),
        ("ben!", "member", "bad_name"),
        ("ana", "member", "name_taken"),
        ("cara", "owner", "bad_role"),
    ],
)
def test_bad_adds_are_refused(store, name, role, code):
    with pytest.raises(OpError) as e:
        store.add(name, role)
    assert e.value.code == code


def test_revoking_a_member_kills_its_token(store):
    t = store.add("ben", "member")
    assert store.revoke("ben") is None
    assert store.resolve(t) is None
    with pytest.raises(OpError) as e:
        store.revoke("ben")
    assert e.value.code == "unknown_token"


def test_revoking_the_admin_rotates_it(store, tmp_path):
    old = (tmp_path / "token").read_text().strip()
    new = store.revoke("ana")
    assert new and new != old
    assert store.resolve(old) is None
    assert store.resolve(new) == Principal("ana", "admin")
    assert (tmp_path / "token").read_text().strip() == new
    assert stat.S_IMODE(os.stat(tmp_path / "token").st_mode) == 0o600


def test_a_hand_edit_applies_and_a_broken_one_keeps_the_last_good_state(
    store, tmp_path
):
    t = store.add("ben", "member")
    path = tmp_path / "tokens.json"
    doc = json.loads(path.read_text())
    doc["tokens"][0]["role"] = "admin"
    doc["default_policy"] = {"claude": {}}
    path.write_text(json.dumps(doc))
    os.utime(path, ns=(1, 1))  # a new mtime, whatever the clock's resolution
    assert store.resolve(t) == Principal("ben", "admin")
    path.write_text("{broken")
    os.utime(path, ns=(2, 2))
    assert store.resolve(t) == Principal("ben", "admin")


def test_unknown_keys_survive_a_write(store, tmp_path):
    store.add("ben", "member")
    path = tmp_path / "tokens.json"
    doc = json.loads(path.read_text())
    doc["default_policy"] = {"claude": {}}
    doc["tokens"][0]["policy"] = {"claude": {}}
    path.write_text(json.dumps(doc))
    os.utime(path, ns=(3, 3))
    store.add("cara", "member")
    doc = json.loads(path.read_text())
    assert doc["default_policy"] == {"claude": {}}
    assert doc["tokens"][0]["policy"] == {"claude": {}}


def test_the_list_names_the_admin_first_and_counts_use_since_start(store):
    t = store.add("ben", "member")
    store.resolve(t)
    rows = store.rows()
    assert [(r["name"], r["role"], r["owner"]) for r in rows] == [
        ("ana", "admin", True),
        ("ben", "member", False),
    ]
    assert rows[1]["used_at"] is not None and rows[0]["used_at"] is None
