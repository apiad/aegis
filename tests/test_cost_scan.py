import gzip
import json

import pytest
from pathlib import Path

from aegis.cost.locality import SPLIT_DIRS
from aegis.cost.scan import Scanner


def _ev(ts: str, **event) -> str:
    return json.dumps({"v": 1, "aegis_ts": ts, "event": event})


def _state_with_acp_session(tmp_path: Path, repo_path: Path) -> Path:
    state = tmp_path / ".aegis" / "state"
    (state / "sessions").mkdir(parents=True)
    (state / "sessions" / "happy-hellman.jsonl").write_text(
        "\n".join(
            [
                _ev(
                    "2026-08-05T17:05:14.000000Z",
                    t="SessionMeta",
                    handle="happy-hellman",
                    provider="opencode",
                    cwd=str(repo_path),
                ),
                # The trap: a message id is present, usage is null.
                _ev(
                    "2026-08-05T17:06:00.000000Z",
                    t="AssistantText",
                    text="Yes",
                    usage=None,
                    message_id="msg_fd2e5cf2c001mIG1UNoXDniZW6",
                ),
                _ev(
                    "2026-08-05T17:12:20.000000Z",
                    t="Result",
                    duration_ms=426134,
                    is_error=False,
                    cost_usd=0.0,
                    usage={
                        "input": 91,
                        "cache_creation": 0,
                        "cache_read": 45440,
                        "output": 163,
                    },
                ),
            ]
        )
        + "\n"
    )
    return state


def test_an_acp_session_is_priced_from_its_result_not_from_its_messages(tmp_path):
    """Trap 6. OpenCode's AssistantText carries a message_id but usage: null.
    A per-message reader takes the dedup key, adds zeros, and says nothing.
    Measured 2026-09-24: 6 of 780 sessions in this workspace are ACP."""
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    state = _state_with_acp_session(tmp_path, repo_path)

    scanner = Scanner("aegis", repo_path, container="repos", since=None, until=None, split_dirs=SPLIT_DIRS)
    scanner.run([], state, foreign=False)

    session = next(iter(scanner.sessions.values()))
    tokens = sum(
        counter["input"] + counter["output"] + counter["cache_read"]
        for counter in session.usage.values()
    )
    assert tokens == 91 + 163 + 45440
    # This session's recorded model is "OpenCode", a harness name the price
    # registry has no entry for, so the call is counted and declared unpriced
    # rather than charged zero.
    assert session.unpriced["calls"] == 1
    assert session.unpriced["tokens"] == 91 + 163 + 45440
    assert sum(c["cost_micro"] for c in session.usage.values()) == 0


def test_a_claude_code_session_is_read_by_message_not_by_result(tmp_path):
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    state = tmp_path / ".aegis" / "state"
    (state / "sessions").mkdir(parents=True)
    (state / "sessions" / "deep-dijkstra.jsonl").write_text(
        "\n".join(
            [
                _ev(
                    "2026-06-01T12:00:00.000000Z",
                    t="SessionMeta",
                    handle="deep-dijkstra",
                    provider="claude-code",
                    cwd=str(repo_path),
                ),
                _ev(
                    "2026-06-01T12:00:01.000000Z",
                    t="SystemInit",
                    model="claude-opus-4-7",
                ),
                _ev(
                    "2026-06-01T12:00:02.000000Z",
                    t="AssistantText",
                    text="hi",
                    message_id="msg_one",
                    usage={
                        "input": 10,
                        "cache_creation": 0,
                        "cache_read": 100,
                        "output": 20,
                    },
                ),
                _ev(
                    "2026-06-01T12:00:03.000000Z",
                    t="Result",
                    duration_ms=900,
                    is_error=False,
                    cost_usd=0.02,
                    usage={
                        "input": 10,
                        "cache_creation": 0,
                        "cache_read": 100,
                        "output": 20,
                    },
                ),
            ]
        )
        + "\n"
    )

    scanner = Scanner("aegis", repo_path, container="repos", since=None, until=None, split_dirs=SPLIT_DIRS)
    scanner.run([], state, foreign=False)

    session = next(iter(scanner.sessions.values()))
    tokens = sum(
        c["input"] + c["output"] + c["cache_read"] for c in session.usage.values()
    )
    assert tokens == 130  # counted once, not 260
    assert sum(c["calls"] for c in session.usage.values()) == 1


def _claude_record(repo_path: Path) -> str:
    return json.dumps(
        {
            "timestamp": "2026-06-01T12:00:00.000Z",
            "type": "assistant",
            "sessionId": "s1",
            "cwd": str(repo_path),
            "message": {
                "id": "msg_shared",
                "model": "claude-opus-4-7",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 5,
                },
            },
        }
    )


def test_the_same_message_in_two_stores_is_counted_once(tmp_path):
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    state = tmp_path / ".aegis" / "state"
    (state / "claude-import").mkdir(parents=True)
    projects = tmp_path / "projects" / "-home-apiad-Workspace"
    projects.mkdir(parents=True)

    line = _claude_record(repo_path)
    (projects / "s1.jsonl").write_text(line + "\n")
    with gzip.open(state / "claude-import" / "s1.jsonl.gz", "wt") as fh:
        fh.write(line + "\n")

    scanner = Scanner("aegis", repo_path, container="repos", since=None, until=None, split_dirs=SPLIT_DIRS)
    scanner.run([(projects, "claude", "c")], state, foreign=True)

    total_calls = sum(
        c["calls"] for s in scanner.sessions.values() for c in s.usage.values()
    )
    assert total_calls == 1
    assert len(scanner.seen) == 1


def test_no_foreign_skips_the_home_projects_store(tmp_path):
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    projects = tmp_path / "projects" / "-home-apiad-Workspace"
    projects.mkdir(parents=True)
    (projects / "s1.jsonl").write_text(_claude_record(repo_path) + "\n")

    scanner = Scanner("aegis", repo_path, container="repos", since=None, until=None, split_dirs=SPLIT_DIRS)
    scanner.run([(projects, "claude", "c")], None, foreign=False)

    assert scanner.sessions == {}
    assert scanner.first_seen is None


def test_a_truncated_json_line_does_not_stop_the_scan(tmp_path):
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    projects = tmp_path / "projects" / "-home-apiad-Workspace"
    projects.mkdir(parents=True)
    (projects / "s1.jsonl").write_text(
        '{"timestamp": "2026-06-01T11:0\n' + _claude_record(repo_path) + "\n"
    )

    scanner = Scanner("aegis", repo_path, container="repos", since=None, until=None, split_dirs=SPLIT_DIRS)
    scanner.run([(projects, "claude", "c")], None, foreign=True)

    assert len(scanner.seen) == 1


def test_a_gemini_session_is_priced_at_gemini_rates_not_at_zero_or_opus(tmp_path):
    """The price registry keys prices by provider, and there is no `gemini`
    model under `claude-code`. Collapsing a model to an opus/sonnet/haiku/gemini
    family and always asking claude-code for the price returns None for Gemini
    and silently charges nothing; defaulting to opus instead would overcharge it
    several times over."""
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    state = tmp_path / ".aegis" / "state"
    (state / "sessions").mkdir(parents=True)
    (state / "sessions" / "merry-minsky.jsonl").write_text(
        "\n".join([
            _ev("2026-08-07T12:00:00.000000Z", t="SessionMeta", handle="merry-minsky",
                provider="gemini", cwd=str(repo_path)),
            _ev("2026-08-07T12:00:01.000000Z", t="SystemInit", model="gemini-3-pro"),
            _ev("2026-08-07T12:05:00.000000Z", t="Result", duration_ms=1000,
                is_error=False,
                usage={"input": 1_000_000, "cache_creation": 0, "cache_read": 0,
                       "output": 0}),
        ]) + "\n"
    )

    scanner = Scanner("aegis", repo_path, container="repos", since=None, until=None, split_dirs=SPLIT_DIRS)
    scanner.run([], state, foreign=False)

    session = next(iter(scanner.sessions.values()))
    cost = sum(c["cost_micro"] for c in session.usage.values()) / 1e6

    from aegis.usage.aggregate import resolve_prices

    gemini = resolve_prices("gemini", "gemini-3-pro")
    opus = resolve_prices("claude-code", "opus")
    assert gemini is not None and opus is not None
    assert cost == pytest.approx(float(gemini.input), rel=1e-9)
    assert cost != pytest.approx(float(opus.input), rel=1e-9)


def test_a_truncated_gzip_archive_does_not_take_the_command_down(tmp_path):
    """DESIGN.md: a damaged file never takes a session down. gzip only finds
    truncation while decompressing, inside the read loop, and raises EOFError,
    which is not an OSError — so guarding only the open() call lets a
    claude-import run that was killed partway through abort the whole
    measurement with a traceback."""
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    state = tmp_path / ".aegis" / "state"
    (state / "claude-import").mkdir(parents=True)

    archive = state / "claude-import" / "s1.jsonl.gz"
    with gzip.open(archive, "wt") as fh:
        for i in range(200):
            fh.write(
                json.dumps(
                    {
                        "timestamp": "2026-06-01T12:00:00.000Z",
                        "type": "assistant",
                        "sessionId": "s1",
                        "cwd": str(repo_path),
                        "message": {
                            "id": f"msg_{i}",
                            "model": "claude-opus-4-7",
                            "usage": {"input_tokens": 10, "output_tokens": 20},
                        },
                    }
                )
                + "\n"
            )
    whole = archive.read_bytes()
    archive.write_bytes(whole[: len(whole) // 2])

    scanner = Scanner("aegis", repo_path, container="repos", since=None, until=None, split_dirs=SPLIT_DIRS)
    scanner.run([], state, foreign=False)  # must not raise

    # Whatever decompressed before the truncation is kept.
    assert len(scanner.seen) > 0


def _gemini_session_across_midnight(tmp_path, repo_path):
    state = tmp_path / ".aegis" / "state"
    (state / "sessions").mkdir(parents=True)
    (state / "sessions" / "merry-minsky.jsonl").write_text(
        "\n".join([
            _ev("2026-08-06T23:50:00.000000Z", t="SessionMeta", handle="merry-minsky",
                provider="gemini", cwd=str(repo_path)),
            _ev("2026-08-06T23:50:01.000000Z", t="SystemInit", model="gemini-3-pro"),
            _ev("2026-08-07T00:10:00.000000Z", t="Result", duration_ms=1000,
                is_error=False,
                usage={"input": 1_000_000, "cache_creation": 0, "cache_read": 0,
                       "output": 0}),
        ]) + "\n"
    )
    return state


def test_a_window_that_cuts_the_session_header_keeps_its_provider_and_cwd(tmp_path):
    """SessionMeta and SystemInit are the only place a session's provider, model
    and cwd are recorded, and per-message events never carry a model. Applying
    the --since filter before reading them silently reprices the session at the
    default model's rate and drops its cwd, which also drops it from share 1.0
    to the record ratio. 217 of 1,169 session logs in this workspace span a day
    boundary, so any --since on one of those days trips it — including the
    --since the tool's own coverage warning tells the reader to pass."""
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    state = _gemini_session_across_midnight(tmp_path, repo_path)

    from aegis.usage.aggregate import resolve_prices

    gemini = resolve_prices("gemini", "gemini-3-pro")
    opus = resolve_prices("claude-code", "opus")
    assert gemini is not None and opus is not None and gemini.input != opus.input

    scanner = Scanner("aegis", repo_path, container="repos", since="2026-08-07", until=None,
                      split_dirs=SPLIT_DIRS)
    scanner.run([], state, foreign=False)

    session = next(iter(scanner.sessions.values()))
    cost = sum(c["cost_micro"] for c in session.usage.values()) / 1e6
    assert session.provider == "gemini"
    assert session.cwd == str(repo_path)
    assert cost == pytest.approx(float(gemini.input), rel=1e-9)
    assert cost != pytest.approx(float(opus.input), rel=1e-9)
