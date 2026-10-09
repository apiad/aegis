"""``aegis import-legacy``: the legacy tree's logs become archived sessions that
the registry lists and folds like its own."""

import json
from pathlib import Path

from typer.testing import CliRunner

from aegis.cli import app
from aegis.registry import Registry
from aegis.roots import make_roots

STEM = "20260730T130711775955Z-placid-perlis"


def _line(ts: str, event: dict) -> str:
    return json.dumps({"v": 1, "aegis_ts": ts, "event": event}) + "\n"


def _raw(obj: dict) -> dict:
    return {"t": "Unknown", "raw": json.dumps(obj)}


def _usage(output: int) -> dict:
    return {"input": 3, "cache_creation": 10, "cache_read": 20, "output": output}


def write_legacy(src: Path) -> None:
    """One legacy log: a prompt Claude echoed, one it did not, a backfilled
    prompt, a tool call, noise the fold ignores, a damaged line, and two
    processes whose running cost totals restart."""
    (src / "sessions").mkdir(parents=True)
    (src / "backfill").mkdir()
    lines = [
        _line(
            "2026-07-30T13:07:11.700000Z",
            {
                "t": "SessionMeta",
                "handle": "placid-perlis",
                "profile": "opus",
                "provider": "claude-code",
                "cwd": "/work/enciclopedia",
                "title": "next chapter",
            },
        ),
        _line(
            "2026-07-30T13:07:12Z",
            {
                "t": "SystemInit",
                "session_id": "sid-1",
                "model": "claude-opus-5",
                "permission_mode": "bypassPermissions",
                "version": "2.1.0",
            },
        ),
        _line("2026-07-30T13:07:13Z", {"t": "UserMessage", "text": "echoed prompt"}),
        _line(
            "2026-07-30T13:07:13.500000Z",
            _raw(
                {
                    "type": "user",
                    "isReplay": True,
                    "message": {"role": "user", "content": "echoed prompt"},
                }
            ),
        ),
        _line(
            "2026-07-30T13:07:14Z",
            _raw({"type": "system", "subtype": "thinking_tokens", "tokens": 5}),
        ),
        _line(
            "2026-07-30T13:07:15Z",
            {
                "t": "AssistantThinking",
                "text": "",
                "message_id": "msg_1",
                "usage": _usage(1),
            },
        ),
        _line(
            "2026-07-30T13:07:16Z",
            {
                "t": "ToolUse",
                "name": "Bash",
                "tool_call_id": "toolu_1",
                "raw_input": {"command": "ls", "description": "List files"},
                "message_id": "msg_1",
                "usage": _usage(4),
            },
        ),
        _line(
            "2026-07-30T13:07:17Z",
            {
                "t": "ToolResult",
                "tool_call_id": "toolu_1",
                "text": "a.md\nb.md",
                "is_error": False,
            },
        ),
        "{not json\n",
        _line(
            "2026-07-30T13:07:18Z",
            {
                "t": "AssistantText",
                "text": "Two files.",
                "message_id": "msg_2",
                "usage": _usage(7),
            },
        ),
        _line(
            "2026-07-30T13:07:19Z",
            {"t": "Result", "is_error": False, "cost_usd": 1.5, "usage": _usage(7)},
        ),
        _line("2026-07-30T13:08:00Z", {"t": "UserMessage", "text": "never echoed"}),
        _line(
            "2026-07-30T13:08:01Z",
            {"t": "AssistantText", "text": "Done.", "usage": _usage(2)},
        ),
        _line(
            "2026-07-30T13:08:02Z",
            {"t": "Result", "is_error": False, "cost_usd": 2.0, "usage": _usage(2)},
        ),
        _line(
            "2026-07-30T14:00:00Z",
            {"t": "SystemInit", "session_id": "sid-2", "model": "claude-opus-5"},
        ),
        _line(
            "2026-07-30T14:00:02Z",
            {"t": "AssistantText", "text": "Resumed.", "usage": _usage(1)},
        ),
        _line(
            "2026-07-30T14:00:03Z",
            {"t": "Result", "is_error": False, "cost_usd": 0.25, "usage": _usage(1)},
        ),
        _line(
            "2026-07-30T14:05:00Z",
            {"t": "SessionClosed", "closed_at": "2026-07-30T14:05:00Z"},
        ),
    ]
    (src / "sessions" / f"{STEM}.jsonl").write_text("".join(lines))
    (src / "backfill" / f"{STEM}.jsonl").write_text(
        _line(
            "2026-07-30T13:59:00Z",
            {"t": "UserMessage", "text": "backfilled prompt", "source": "operator"},
        )
    )
    # A session opened and never used.
    (src / "sessions" / "20260730T150000000000Z-idle-ivy.jsonl").write_text(
        _line(
            "2026-07-30T15:00:00Z",
            {"t": "SessionMeta", "handle": "idle-ivy", "cwd": "/work"},
        )
    )


def invoke(*args: str):
    return CliRunner().invoke(app, list(args), env={"COLUMNS": "200"})


def boot(root: Path) -> Registry:
    r = Registry(make_roots(root, None), lambda ch, ops: None, "claude")
    r.boot()
    return r


def test_a_legacy_log_reads_like_an_archived_session(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    write_legacy(tmp_path / "legacy")

    res = invoke("import-legacy", str(tmp_path / "legacy"), "--root", str(tmp_path))
    assert res.exit_code == 0, res.output
    assert res.output.strip() == (
        "1 imported, 0 already imported, 1 empty skipped, 1 damaged lines skipped"
    )

    r = boot(tmp_path)
    [meta] = r.archive(None, 10, None)[0]
    assert meta["handle"] == "placid-perlis"
    assert meta["harness_label"] == "Claude Code"
    assert meta["title"] == "next chapter"
    assert meta["cwd"] == "/work/enciclopedia"
    assert meta["permission"] == "full"
    assert meta["claude_session_id"] == "sid-2"
    assert meta["cost_usd"] == 2.25  # 2.0 for the first process, 0.25 the second
    assert meta["log_id"].startswith("20260730-130711-")

    entries = r.transcript(meta["log_id"])()["entries"]
    users = [e["md"] for e in entries if e["kind"] == "user"]
    assert users == ["echoed prompt", "never echoed", "backfilled prompt"]
    assert [e["md"] for e in entries if e["kind"] == "prose"] == [
        "Two files.",
        "Done.",
        "Resumed.",
    ]
    [tool] = [e for e in entries if e["kind"] == "tool"]
    assert tool["status"] == "ok"
    # An empty thinking block says nothing and makes no row.
    assert not [e for e in entries if e["kind"] == "thinking"]
    summaries = [e["summary"] for e in entries if e["kind"] == "system"]
    assert "skipped 1 damaged lines" in summaries
    assert summaries[-1] == "closed"


def test_a_second_run_imports_only_what_is_new(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    write_legacy(tmp_path / "legacy")
    invoke("import-legacy", str(tmp_path / "legacy"), "--root", str(tmp_path))
    store = next((tmp_path / ".aegis" / "state" / "transcripts").glob("*.jsonl"))
    before = store.read_bytes()

    res = invoke("import-legacy", str(tmp_path / "legacy"), "--root", str(tmp_path))
    assert res.output.strip().startswith("0 imported, 1 already imported")
    assert store.read_bytes() == before


def test_an_imported_handle_never_takes_a_live_one(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    write_legacy(tmp_path / "legacy")
    sessions = tmp_path / ".aegis" / "state" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "20261001-000000-aaaaaa.json").write_text(
        json.dumps(
            {
                "log_id": "20261001-000000-aaaaaa",
                "handle": "placid-perlis",
                "archived": True,
            }
        )
    )
    invoke("import-legacy", str(tmp_path / "legacy"), "--root", str(tmp_path))
    # On disk, not after a boot: boot re-mints a duplicate in memory only, so
    # the handle would change on every start.
    handles = sorted(
        json.loads(p.read_text())["handle"] for p in sessions.glob("*.json")
    )
    assert handles == ["placid-perlis", "placid-perlis-2"]


def test_it_refuses_a_target_that_holds_legacy_state(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    write_legacy(tmp_path / "legacy")
    state = tmp_path / ".aegis" / "state"
    state.mkdir(parents=True)
    (state / "history_index.json").write_text("{}")
    res = invoke("import-legacy", str(tmp_path / "legacy"), "--root", str(tmp_path))
    assert res.exit_code == 2
    assert "holds legacy state" in res.output
    assert not (state / "sessions").exists()


def test_it_refuses_a_directory_with_no_sessions(tmp_path):
    res = invoke("import-legacy", str(tmp_path), "--root", str(tmp_path))
    assert res.exit_code == 2
    assert "not a legacy state directory" in res.output
