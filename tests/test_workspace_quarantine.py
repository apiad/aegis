"""An unreadable workspace snapshot must not stop aegis from starting.

`workspace.json` is a cache of the tab roster, terminals and open files.
Every conversation lives in `sessions/*.jsonl` keyed by `log_id` and is
reachable from Ctrl+R, so losing the snapshot costs a layout, not a
transcript. Refusing to boot over it is out of proportion, and on a
`WORKSPACE_VERSION` bump it would lock every root out at once.

Two causes, two answers, because they are not the same event.

A version mismatch is ordinary. It happens on any upgrade that changes the
format and on every downgrade. It is treated as no snapshot at all, in
silence, the way `state/history.py` already treats an index it cannot
read.

Unparseable JSON is not ordinary: the writer uses a temp file and
`os.replace`, so a crash mid-write cannot produce it. The file is moved
aside rather than dropped, following the `.corrupt<N>` convention
`state/repair.py` already uses for damaged logs, and the user is told
where it went.
"""
from __future__ import annotations

import json

import pytest

from aegis.state.workspace import (
    CorruptWorkspace, WORKSPACE_VERSION, WorkspaceVersionMismatch,
    Workspace, WorkspaceTab, load, load_or_quarantine, save,
)


def _sd(tmp_path):
    sd = tmp_path / ".aegis" / "state"
    sd.mkdir(parents=True)
    return sd


def _good(sd):
    save(sd, Workspace(tabs=[WorkspaceTab(
        handle="h1", profile="default", order=0, provider="claude-code",
        session_id="sid", created_at="2026-09-12T00:00:00Z", log_id="lg")]))


def test_a_version_mismatch_is_its_own_error(tmp_path):
    """Separable from unparseable JSON, and by type rather than by the
    wording of a message."""
    sd = _sd(tmp_path)
    (sd / "workspace.json").write_text(
        json.dumps({"version": WORKSPACE_VERSION + 99, "tabs": []}))

    with pytest.raises(WorkspaceVersionMismatch):
        load(sd)
    # Still a CorruptWorkspace, so every existing handler keeps working.
    assert issubclass(WorkspaceVersionMismatch, CorruptWorkspace)


def test_a_version_mismatch_reads_as_no_snapshot_and_is_left_alone(tmp_path):
    sd = _sd(tmp_path)
    payload = json.dumps({"version": WORKSPACE_VERSION + 99, "tabs": []})
    (sd / "workspace.json").write_text(payload)

    ws, quarantined = load_or_quarantine(sd)

    assert ws is None
    assert quarantined is None, "a version bump must not litter .corrupt files"
    assert (sd / "workspace.json").read_text() == payload, \
        "the snapshot was modified on what is an ordinary upgrade"


def test_unparseable_json_is_moved_aside_with_its_bytes_intact(tmp_path):
    sd = _sd(tmp_path)
    (sd / "workspace.json").write_text("{not json")

    ws, quarantined = load_or_quarantine(sd)

    assert ws is None
    assert quarantined is not None, "the corrupt file was dropped, not kept"
    assert quarantined.read_text() == "{not json", \
        "the bytes we could not parse must survive for inspection"
    assert not (sd / "workspace.json").exists(), \
        "the broken file must not be left where the next boot re-reads it"


def test_a_second_corruption_does_not_overwrite_the_first(tmp_path):
    sd = _sd(tmp_path)
    (sd / "workspace.json").write_text("first damage")
    _ws, first = load_or_quarantine(sd)
    (sd / "workspace.json").write_text("second damage")
    _ws, second = load_or_quarantine(sd)

    assert first != second
    assert first.read_text() == "first damage"
    assert second.read_text() == "second damage"


def test_a_healthy_snapshot_is_returned_untouched(tmp_path):
    """The guard against a fix that quarantines everything."""
    sd = _sd(tmp_path)
    _good(sd)

    ws, quarantined = load_or_quarantine(sd)

    assert quarantined is None
    assert ws is not None and [t.handle for t in ws.tabs] == ["h1"]
    assert (sd / "workspace.json").exists()


def test_no_snapshot_at_all_is_not_a_quarantine(tmp_path):
    sd = _sd(tmp_path)
    assert load_or_quarantine(sd) == (None, None)


@pytest.mark.asyncio
async def test_the_tui_boots_on_a_corrupt_snapshot_and_says_where_it_went(
        tmp_path, monkeypatch):
    """The whole point. Before this, `aegis` in a project with a damaged
    snapshot exited rather than starting, and once the boot moved into the
    daemon it exited 0 having said nothing at all.

    The notice matters as much as the boot: tabs that vanish without a
    word read as lost work, which is the complaint this began as.
    """
    from aegis.config import Agent
    from aegis.events import AssistantText, Result
    from aegis.tui.app import AegisApp

    monkeypatch.chdir(tmp_path)
    sd = tmp_path / ".aegis" / "state"
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text("{not json")

    class _Session:
        async def start(self): ...
        async def send(self, t): ...
        async def close(self): ...

        async def events(self):
            yield AssistantText("ok", usage=None)
            yield Result(duration_ms=1, is_error=False)

    class _MCP:
        url = "http://127.0.0.1:0/mcp/"

        def bind(self, b): ...
        async def start(self): ...
        async def stop(self): ...

    agent = Agent(harness="claude-code", model="opus", effort="high",
                  permission="auto")
    app = AegisApp({"default": agent}, "default",
                   lambda *a, **kw: _Session(), _MCP())
    notices: list[str] = []
    # Patched on the class, before run_test: the boot that fires this
    # notice runs on mount, so an instance attribute set inside the
    # context manager is already too late.
    monkeypatch.setattr(AegisApp, "notify",
                        lambda self, msg, **kw: notices.append(str(msg)))

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.is_running, "the TUI refused to start on a bad snapshot"

    joined = " ".join(notices)
    assert "workspace" in joined.lower(), \
        f"the user was never told the snapshot was dropped: {notices}"
    assert any(p.name.startswith("workspace.json.corrupt")
               for p in sd.iterdir()), "the corrupt bytes were not kept"
