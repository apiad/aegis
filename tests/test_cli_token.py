from typer.testing import CliRunner

from aegis.cli import app


def run(root, *args):
    return CliRunner().invoke(
        app, ["token", *args, "--root", str(root)], env={"COLUMNS": "200"}
    )


def test_the_token_commands(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    added = run(tmp_path, "add", "ben")
    assert added.exit_code == 0, added.output
    token = added.stdout.strip()
    assert len(token) >= 40 and "\n" not in token
    listed = run(tmp_path, "list").stdout
    assert "ben" in listed and "member" in listed
    assert "every pool closed" in listed and "never gated" in listed
    assert run(tmp_path, "revoke", "ben").exit_code == 0
    assert "ben" not in run(tmp_path, "list").stdout
    gone = run(tmp_path, "revoke", "ben")
    assert gone.exit_code == 1 and "no token named ben" in gone.output


def test_revoking_the_admin_prints_its_new_token(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    import getpass

    old = None
    run(tmp_path, "list")  # creates the admin token
    old = (tmp_path / ".aegis" / "state" / "token").read_text().strip()
    out = run(tmp_path, "revoke", getpass.getuser())
    assert out.exit_code == 0
    new = out.stdout.strip()
    assert new and new != old
    assert (tmp_path / ".aegis" / "state" / "token").read_text().strip() == new


def test_token_add_linux_user_needs_the_mode(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    out = run(tmp_path, "add", "ben", "--linux-user", "ben")
    assert out.exit_code == 1 and "needs multi_user" in out.output


def test_token_list_shows_the_linux_user_in_the_mode(tmp_path):
    import os
    import pwd

    from aegis import users

    member = next(
        (
            pw.pw_name
            for pw in pwd.getpwall()
            if users.uid_min() <= pw.pw_uid < 60000 and pw.pw_uid != os.getuid()
        ),
        None,
    )
    if member is None:
        import pytest

        pytest.skip("no regular user other than the test user on this host")
    (tmp_path / ".aegis.yaml").write_text("multi_user:\n")
    added = run(tmp_path, "add", "mate", "--linux-user", member)
    assert added.exit_code == 0, added.output
    line = next(x for x in run(tmp_path, "list").stdout.splitlines() if "mate" in x)
    assert member in line and "member" in line
