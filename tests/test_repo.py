"""Repository-wide rules that a reader would otherwise have to remember."""

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from aegis.cli import app
from aegis.roots import LEGACY_MARKERS, legacy_state

ROOT = Path(__file__).resolve().parents[1]
OLD_NAME = "aegis" + "2"  # spelled apart so this file does not trip its own test


def test_the_working_name_of_the_rewrite_appears_nowhere():
    """aegis was built as a second tree under a working name; since 2.0 it is
    aegis, and the name left nothing behind: no path, no identifier, no doc."""
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    hits = [f for f in files if OLD_NAME in f]
    for f in files:
        p = ROOT / f
        if p.suffix in (".woff2", ".png", ".svg") or not p.is_file():
            continue
        try:
            if OLD_NAME in p.read_text():
                hits.append(f)
        except UnicodeDecodeError:
            continue
    assert not hits, f"{OLD_NAME} appears in {sorted(set(hits))}"


def test_legacy_state_is_recognised_by_its_markers(tmp_path: Path):
    assert legacy_state(tmp_path) == []
    (tmp_path / "daemon.lock").touch()
    (tmp_path / "comms").mkdir()
    assert legacy_state(tmp_path) == ["daemon.lock", "comms"]
    assert "sessions" not in LEGACY_MARKERS and "token" not in LEGACY_MARKERS


def test_serve_refuses_a_state_directory_holding_legacy_state(tmp_path: Path):
    state = tmp_path / ".aegis" / "state"
    state.mkdir(parents=True)
    (state / "workspace.json").write_text("{}")
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    result = CliRunner().invoke(app, ["serve", "--root", str(tmp_path), "--port", "1"])
    assert result.exit_code == 1
    assert "legacy-state" in result.output and "workspace.json" in result.output
