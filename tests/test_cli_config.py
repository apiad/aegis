import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from aegis.cli import app

pytestmark = pytest.mark.slow  # every init probes both fakes


def invoke(*args: str, input: str | None = None):
    return CliRunner().invoke(app, list(args), input=input, env={"COLUMNS": "200"})


def fakes(claude: str, opencode: str) -> list[str]:
    return ["--claude", claude, "--opencode", opencode]


def test_init_yes_writes_a_config_the_doctor_passes(
    tmp_path, fake_claude, fake_opencode
):
    root = tmp_path / "ws"
    root.mkdir()
    r = invoke("init", "--root", str(root), "--yes", *fakes(fake_claude, fake_opencode))
    assert r.exit_code == 0, r.output
    data = YAML(typ="safe").load((root / ".aegis.yaml").read_text())
    assert data["default_agent"] == "opus"
    assert data["agents"]["fake-flash"]["model"] == "opencode-go/fake-flash"
    assert data["queues"]["general"] == {"agent": "opus", "max_parallel": 3}
    assert "0 errors" in r.output


def test_init_asks_with_the_proposal_filled_in(tmp_path, fake_claude, fake_opencode):
    root = tmp_path / "ws"
    root.mkdir()
    # opus: add, model, effort, permission; fake-flash: add, model, effort,
    # permission; default; queue; workers.
    answers = "\n\n\nread\nn\nopus\ny\n5\n"
    r = invoke(
        "init", "--root", str(root), *fakes(fake_claude, fake_opencode), input=answers
    )
    assert r.exit_code == 0, r.output
    data = YAML(typ="safe").load((root / ".aegis.yaml").read_text())
    assert list(data["agents"]) == ["opus"]
    assert data["agents"]["opus"]["permission"] == "read"
    assert data["queues"]["general"]["max_parallel"] == 5


def test_init_refuses_an_existing_file(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    r = invoke(
        "init", "--root", str(tmp_path), "--yes", *fakes(fake_claude, fake_opencode)
    )
    assert r.exit_code == 1 and "aegis doctor" in r.output
    assert (tmp_path / ".aegis.yaml").read_text() == "agents: {}\n"


def test_init_under_a_configured_parent_asks_first(
    tmp_path, fake_claude, fake_opencode
):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    child = tmp_path / "child"
    child.mkdir()
    r = invoke(
        "init", "--root", str(child), *fakes(fake_claude, fake_opencode), input="n\n"
    )
    assert r.exit_code == 1 and str(tmp_path / ".aegis.yaml") in r.output
    assert not (child / ".aegis.yaml").exists()


def test_init_with_no_harness_writes_nothing(tmp_path):
    r = invoke(
        "init",
        "--root",
        str(tmp_path),
        "--yes",
        "--claude",
        str(tmp_path / "x"),
        "--opencode",
        str(tmp_path / "y"),
    )
    assert r.exit_code == 1 and "No harness" in r.output
    assert not (tmp_path / ".aegis.yaml").exists()


def test_doctor_exits_1_on_an_error_and_0_when_healthy(
    tmp_path, fake_claude, fake_opencode
):
    r = invoke("doctor", "--root", str(tmp_path), *fakes(fake_claude, fake_opencode))
    assert r.exit_code == 1 and "aegis init" in r.output
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    r = invoke("doctor", "--root", str(tmp_path), *fakes(fake_claude, fake_opencode))
    assert r.exit_code == 0, r.output
    assert "0 errors, 0 warnings" in r.output
