import json
import os
import pwd
import stat

import pytest

from aegis.ops import OpError, Principal
from aegis import users
from aegis.tokens import NAME, TokenStore


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


LINES = {"claude": {"mode": "lines", "lines": {"session": {"launch": 50, "stop": 90}}}}


def test_a_member_gets_its_own_policy_else_the_default(store):
    store.add("ben", "member")
    store.add("cara", "member")
    assert store.policy_of("ben") == {}  # no default: every pool closed
    store.set_default_policy({"claude": {"mode": "open"}})
    store.set_policy("cara", LINES)
    assert store.policy_of("ben") == {"claude": {"mode": "open"}}
    assert store.policy_of("cara") == LINES
    store.set_policy("cara", None)
    assert store.policy_of("cara") == {"claude": {"mode": "open"}}


def test_an_admin_is_never_gated_whatever_the_default(store):
    store.add("dan", "admin")
    store.set_default_policy(LINES)
    assert store.policy_of("ana") is None
    assert store.policy_of("dan") is None
    for name in ("ana", "dan"):
        with pytest.raises(OpError) as e:
            store.set_policy(name, LINES)
        assert e.value.code == "never_gated"


def test_a_revoked_principal_starts_nothing(store):
    store.add("ben", "member")
    store.set_default_policy({"claude": {"mode": "open"}})
    store.revoke("ben")
    assert store.policy_of("ben") == {}


def test_an_invalid_policy_is_refused_with_the_reasons(store):
    store.add("ben", "member")
    bad = {
        "claude": {"mode": "lines", "lines": {"session": {"launch": 95, "stop": 90}}}
    }
    for set_ in (
        lambda: store.set_policy("ben", bad),
        lambda: store.set_default_policy(bad),
    ):
        with pytest.raises(OpError) as e:
            set_()
        assert (
            e.value.code == "bad_policy" and "launch is below stop" in e.value.message
        )
    with pytest.raises(OpError) as e:
        store.set_policy("nobody", LINES)
    assert e.value.code == "unknown_token"


def test_a_policy_survives_a_revoke_of_another_token_and_a_reload(store, tmp_path):
    store.add("ben", "member")
    store.add("cara", "member")
    store.set_policy("ben", LINES)
    store.set_default_policy({"claude": {"mode": "open"}})
    store.revoke("cara")
    again = TokenStore(tmp_path, owner="ana")
    assert again.policy_of("ben") == LINES
    assert again.default_policy() == {"claude": {"mode": "open"}}
    rows = {r["name"]: r for r in again.rows()}
    assert rows["ben"]["policy"] == LINES and rows["ana"]["policy"] is None


@pytest.fixture
def member_user():
    """A regular account other than the test user's: the Linux user a member
    token can name on this host."""
    me = os.getuid()
    for pw in pwd.getpwall():
        if pw.pw_uid >= users.uid_min() and pw.pw_uid != me and pw.pw_uid < 60000:
            if NAME.match(pw.pw_name):
                return pw.pw_name
    pytest.skip("no regular user other than the test user on this host")


@pytest.fixture
def store_mu(tmp_path):
    return TokenStore(tmp_path, "owner", multi_user=True)


def test_add_in_the_mode_names_the_user_after_the_token(store_mu, member_user):
    store_mu.add(member_user, "member")
    assert store_mu.linux_user_of(member_user) == member_user
    assert store_mu.rows()[1]["linux_user"] == member_user


def test_add_takes_another_user_with_linux_user(store_mu, member_user):
    store_mu.add("laptop", "member", linux_user=member_user)
    assert store_mu.linux_user_of("laptop") == member_user


@pytest.mark.parametrize("bad", ["aegis-no-such-user", "root", "daemon"])
def test_add_refuses_a_user_that_is_missing_or_not_allowed(store_mu, bad):
    with pytest.raises(OpError) as e:
        store_mu.add("ben", "member", linux_user=bad)
    assert e.value.code in ("no_such_user", "user_not_allowed")
    assert store_mu.rows()[1:] == []


def test_add_refuses_the_servers_own_user(store_mu):
    with pytest.raises(OpError) as e:
        store_mu.add("me", "member", linux_user=pwd.getpwuid(os.getuid()).pw_name)
    assert e.value.code == "user_not_allowed"


def test_no_linux_user_is_for_admins_only(store_mu):
    with pytest.raises(OpError) as e:
        store_mu.add("ben", "member", no_linux_user=True)
    assert e.value.code == "member_needs_user"
    store_mu.add("other-server", "admin", no_linux_user=True)
    assert store_mu.linux_user_of("other-server") is None


def test_outside_the_mode_linux_user_is_an_error_and_the_field_is_ignored(
    tmp_path, member_user
):
    off = TokenStore(tmp_path, "owner")
    with pytest.raises(OpError) as e:
        off.add("ben", "member", linux_user=member_user)
    assert e.value.code == "needs_multi_user"
    TokenStore(tmp_path, "owner", multi_user=True).add(member_user, "member")
    assert off.linux_user_of(member_user) is None


def test_sign_in_refuses_a_member_whose_user_is_gone(
    tmp_path, store_mu, member_user, caplog
):
    token = store_mu.add(member_user, "member")
    doc = json.loads((tmp_path / "tokens.json").read_text())
    doc["tokens"][0]["linux_user"] = "aegis-no-such-user"
    (tmp_path / "tokens.json").write_text(json.dumps(doc))
    os.utime(tmp_path / "tokens.json", ns=(1, 1))
    assert store_mu.resolve(token) is None
    assert "aegis-no-such-user" in caplog.text
    assert store_mu.missing_users() == [(member_user, "aegis-no-such-user")]


def test_sign_in_refuses_a_member_token_from_before_the_mode(tmp_path, caplog):
    token = TokenStore(tmp_path, "owner").add("ben", "member")
    assert TokenStore(tmp_path, "owner", multi_user=True).resolve(token) is None
    assert "revoke it and add it again with --linux-user" in caplog.text


def test_an_admin_without_a_user_signs_in_in_the_mode(store_mu):
    token = store_mu.add("other-server", "admin", no_linux_user=True)
    assert store_mu.resolve(token) == Principal("other-server", "admin")
