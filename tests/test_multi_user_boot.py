from typer.testing import CliRunner

from aegis.cli import app


def test_serve_in_multi_user_mode_without_caps_exits_naming_the_unit_lines(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("multi_user:\n")
    r = CliRunner().invoke(app, ["serve", "--root", str(tmp_path), "--port", "1"])
    assert r.exit_code == 1
    assert "AmbientCapabilities=CAP_SETUID CAP_SETGID CAP_SETPCAP" in r.output


def test_serve_with_a_bad_multi_user_block_exits_with_the_error(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("multi_user: true\n")
    r = CliRunner().invoke(app, ["serve", "--root", str(tmp_path), "--port", "1"])
    assert r.exit_code == 1 and "multi_user is a map" in r.output
