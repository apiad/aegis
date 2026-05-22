import json
from dataclasses import asdict
from pathlib import Path

import pytest

from aegis.state.workspace import (
    Workspace, WorkspaceTab, WorkspaceTerminal, load, save,
)
from aegis.terminal.manager import CommandRecord, TerminalManager


def test_workspace_roundtrips_terminals(tmp_path: Path):
    ws = Workspace(
        active_handle="alice",
        tabs=[WorkspaceTab(handle="alice", profile="default", order=0,
                           provider="claude-code", session_id=None,
                           created_at="2026-05-22T00:00:00Z")],
        terminals=[
            WorkspaceTerminal(name="build", shell="/bin/bash",
                              cwd="/tmp", created_at="2026-05-22T00:01:00Z"),
            WorkspaceTerminal(name="dev", shell="/bin/zsh",
                              cwd="/srv", created_at="2026-05-22T00:02:00Z"),
        ],
    )
    save(tmp_path, ws)
    loaded = load(tmp_path)
    assert loaded is not None
    assert [t.name for t in loaded.terminals] == ["build", "dev"]
    assert loaded.terminals[1].shell == "/bin/zsh"
    assert loaded.terminals[1].cwd == "/srv"


def test_workspace_load_tolerates_missing_terminals_key(tmp_path: Path):
    """Older workspace.json files (pre-terminals) have no terminals key
    — we load them with an empty list rather than raising CorruptWorkspace."""
    (tmp_path / "workspace.json").write_text(json.dumps({
        "version": 1, "saved_at": "2026-05-22T00:00:00Z",
        "active_handle": None, "tabs": [],
    }))
    loaded = load(tmp_path)
    assert loaded is not None
    assert loaded.terminals == []


@pytest.mark.asyncio
async def test_stale_in_flight_marked_killed_by_restart(tmp_path: Path):
    state_dir = tmp_path / "s"
    term_dir = state_dir / "build"
    term_dir.mkdir(parents=True)
    (term_dir / "meta.json").write_text(json.dumps({
        "name": "build", "shell": "/bin/bash", "cwd": str(tmp_path),
        "started_at": "2026-05-21T00:00:00Z", "version": 1,
    }))
    stale = {
        "seq": 0, "cmd": "sleep 100", "writer": "agent:a",
        "started_at": "2026-05-21T00:00:01Z", "finished_at": None,
        "duration_s": None, "exit": None,
        "stdout": "", "stderr": "", "killed_by_restart": False,
        "timed_out": False,
    }
    (term_dir / "ledger.jsonl").write_text(json.dumps(stale) + "\n")

    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="build", shell="/bin/bash")
    recs = mgr.read("build", last_n=10)
    assert recs[0].killed_by_restart is True
    assert recs[0].exit is None
    await mgr.close("build")


@pytest.mark.asyncio
async def test_completed_records_not_touched_on_respawn(tmp_path: Path):
    state_dir = tmp_path / "s"
    term_dir = state_dir / "build"
    term_dir.mkdir(parents=True)
    (term_dir / "meta.json").write_text(json.dumps({
        "name": "build", "shell": "/bin/bash", "cwd": str(tmp_path),
        "started_at": "2026-05-21T00:00:00Z", "version": 1,
    }))
    done = CommandRecord(
        seq=0, cmd="echo done", writer="human",
        started_at="2026-05-21T00:00:01Z",
        finished_at="2026-05-21T00:00:02Z",
        duration_s=1.0, exit=0,
        stdout="done\n", stderr="",
    )
    (term_dir / "ledger.jsonl").write_text(json.dumps(asdict(done)) + "\n")

    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="build", shell="/bin/bash")
    recs = mgr.read("build", last_n=10)
    assert recs[0].killed_by_restart is False
    assert recs[0].exit == 0
    # next_seq picks up where the old ledger left off.
    rec = await mgr.run("build", "true", writer="human")
    assert rec.seq == 1
    await mgr.close("build")
