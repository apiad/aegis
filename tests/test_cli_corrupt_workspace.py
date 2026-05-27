from typer.testing import CliRunner

from aegis.cli import app
from aegis.state.workspace import state_dir

runner = CliRunner()


def test_corrupt_workspace_exits_nonzero_with_hint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Write a minimal .aegis.yaml so config load doesn't fail first.
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: default\n"
        "agents:\n"
        "  default:\n"
        "    provider: claude-code\n"
        "    model: opus\n"
    )
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text("{not json")
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "workspace.json" in result.output + (result.stderr or "")
    assert "--clean" in result.output + (result.stderr or "")
