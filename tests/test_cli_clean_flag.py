import re

from typer.testing import CliRunner

from aegis.cli import app
from aegis.state.workspace import (
    Workspace, WorkspaceTab, save, state_dir,
)
from aegis.tui.app import pick_workspace_to_resume


runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def test_clean_flag_shows_in_help():
    """--clean appears in the root command help."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--clean" in _plain(result.output)


def test_pick_workspace_returns_none_when_clean(tmp_path):
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="x", tabs=[]))
    assert pick_workspace_to_resume(sd, clean=True) is None


def test_pick_workspace_returns_workspace_when_not_clean(tmp_path):
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="x", tabs=[]))
    out = pick_workspace_to_resume(sd, clean=False)
    assert out is not None
    assert out.active_handle == "x"


def test_pick_workspace_returns_none_when_missing(tmp_path):
    assert pick_workspace_to_resume(state_dir(tmp_path), clean=False) is None
