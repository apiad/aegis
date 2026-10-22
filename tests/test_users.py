import os
import pwd
from pathlib import Path

import pytest

from aegis import users
from aegis.ops import OpError


def defs(tmp_path, text):
    p = tmp_path / "login.defs"
    p.write_text(text)
    return p


def test_uid_min_reads_login_defs_and_defaults_to_1000(tmp_path):
    assert users.uid_min(defs(tmp_path, "# c\nUID_MIN\t\t 1200\n")) == 1200
    assert users.uid_min(defs(tmp_path, "UID_MAX 60000\n")) == 1000
    assert users.uid_min(tmp_path / "missing") == 1000


def test_env_path_reads_env_path(tmp_path):
    p = defs(tmp_path, "ENV_PATH\tPATH=/usr/local/bin:/usr/bin:/bin:/usr/games\n")
    assert users.env_path(p) == "/usr/local/bin:/usr/bin:/bin:/usr/games"
    assert users.env_path(tmp_path / "missing") == "/usr/local/bin:/usr/bin:/bin"


def test_allowed_refuses_a_missing_user_root_and_the_server():
    me = pwd.getpwuid(os.getuid())
    with pytest.raises(OpError) as e:
        users.allowed("aegis-no-such-user", server_uid=os.getuid())
    assert e.value.code == "no_such_user"
    with pytest.raises(OpError) as e:
        users.allowed("root", server_uid=os.getuid())
    assert e.value.code == "user_not_allowed"
    with pytest.raises(OpError) as e:
        users.allowed(me.pw_name, server_uid=me.pw_uid)
    assert e.value.code == "user_not_allowed"


def test_allowed_refuses_a_uid_below_uid_min():
    assert pwd.getpwnam("daemon").pw_uid < 1000  # uid 1 on Debian and Ubuntu
    with pytest.raises(OpError) as e:
        users.allowed("daemon", server_uid=os.getuid())
    assert (
        e.value.code == "user_not_allowed" and str(users.uid_min()) in e.value.message
    )


def test_allowed_accepts_a_regular_user_other_than_the_server():
    me = pwd.getpwuid(os.getuid())
    if me.pw_uid < 1000:
        pytest.skip("the test user is a system account")
    assert users.allowed(me.pw_name, server_uid=me.pw_uid + 1).pw_uid == me.pw_uid


@pytest.mark.parametrize(
    "data,want,err",
    [
        ({}, None, None),
        ({"multi_user": None}, users.MultiUser(), None),
        ({"multi_user": {}}, users.MultiUser(), None),
        (
            {"multi_user": {"env_file": "/etc/aegis/pool.env"}},
            users.MultiUser(env_file=Path("/etc/aegis/pool.env")),
            None,
        ),
        (
            {"multi_user": {"env_file": "pool.env"}},
            None,
            "multi_user.env_file must be an absolute path",
        ),
        ({"multi_user": {"envfile": "/x"}}, None, "multi_user: unknown key envfile"),
        (
            {"multi_user": True},
            None,
            "multi_user is a map, e.g. multi_user: {env_file: /etc/aegis/pool.env}",
        ),
    ],
)
def test_multi_user_from(data, want, err):
    assert users.multi_user_from(data) == (want, err)


def test_parse_env_strips_one_pair_of_quotes_and_skips_bad_lines(caplog):
    text = "# pooled\n\nA=1\nB=\"two words\"\nC='x'\nnot a line\nD=a=b\n9X=no\n"
    assert users.parse_env(text) == {"A": "1", "B": "two words", "C": "x", "D": "a=b"}
    assert "line 6" in caplog.text and "line 8" in caplog.text


def test_pool_reads_again_when_the_file_changes(tmp_path):
    p = tmp_path / "pool.env"
    p.write_text("A=1\n")
    pool = users.Pool(p)
    assert pool.get() == {"A": "1"}
    p.write_text("A=2\n")
    os.utime(p, ns=(1, 1))
    assert pool.get() == {"A": "2"}
    assert users.Pool(None).get() == {}
    assert users.Pool(tmp_path / "gone").get() == {}


FULL = "CapEff:\t00000000000001c0\nCapAmb:\t00000000000001c0\n"
NONE = "CapEff:\t0000000000000000\nCapAmb:\t0000000000000000\n"


def test_caps_of_reads_effective_and_ambient():
    assert users.caps_of(FULL) == (0x1C0, 0x1C0)


def test_boot_problems_names_the_unit_lines_when_caps_are_missing(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    out = users.boot_problems(state, users.MultiUser(), status=NONE, uid=os.getuid())
    text = "\n".join(out)
    assert "CAP_SETUID CAP_SETGID CAP_SETPCAP" in text
    assert all(line in text for line in users.UNIT_LINES)
    assert "aegis service install --multi-user" in text


def test_boot_problems_refuses_root(tmp_path):
    out = users.boot_problems(tmp_path, users.MultiUser(), status=FULL, uid=0)
    assert any("root" in p for p in out)


def test_boot_problems_tightens_the_state_mode(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    assert (
        users.boot_problems(state, users.MultiUser(), status=FULL, uid=os.getuid())
        == []
    )
    assert state.stat().st_mode & 0o777 == 0o700


def test_boot_problems_refuses_a_state_another_user_owns(tmp_path):
    out = users.boot_problems(
        tmp_path, users.MultiUser(), status=FULL, uid=os.getuid() + 1
    )
    assert any(str(tmp_path) in p for p in out)


def test_boot_problems_refuses_a_pool_file_others_can_read(tmp_path):
    pool = tmp_path / "pool.env"
    pool.write_text("A=1\n")
    pool.chmod(0o644)
    mu = users.MultiUser(pool)
    out = users.boot_problems(tmp_path, mu, status=FULL, uid=os.getuid())
    assert any("pool.env" in p and "0600" in p for p in out)
    pool.chmod(0o600)
    assert users.boot_problems(tmp_path, mu, status=FULL, uid=os.getuid()) == []
    missing = users.MultiUser(tmp_path / "gone.env")
    out = users.boot_problems(tmp_path, missing, status=FULL, uid=os.getuid())
    assert any("gone.env" in p for p in out)
