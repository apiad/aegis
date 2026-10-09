import gzip
import json
from pathlib import Path

import pytest

from aegis.usage.locality import SPLIT_DIRS
from aegis.usage.prices import prices_for
from aegis.usage.scan import Scanner

from .stores import assistant as _assistant
from .stores import codex_store, codex_turn, codex_usage, opencode_step, opencode_store
from .stores import store as _store


def _scanner(repo_path: Path, since: str | None = None) -> Scanner:
    return Scanner(
        "aegis",
        repo_path,
        container="repos",
        since=since,
        until=None,
        split_dirs=SPLIT_DIRS,
    )


def _calls(scanner: Scanner) -> int:
    return sum(c["calls"] for s in scanner.sessions.values() for c in s.usage.values())


def _cost(scanner: Scanner) -> float:
    return (
        sum(
            c["cost_micro"] for s in scanner.sessions.values() for c in s.usage.values()
        )
        / 1e6
    )


@pytest.fixture
def repo_path(tmp_path):
    p = tmp_path / "repos" / "aegis"
    p.mkdir(parents=True)
    return p


def _claude_record(repo_path: Path, mid: str = "msg_shared") -> str:
    return json.dumps(
        {
            "timestamp": "2026-06-01T12:00:00.000Z",
            "sessionId": "s1",
            "cwd": str(repo_path),
            **_assistant(mid, cache_creation_input_tokens=5),
        }
    )


def test_the_same_message_in_two_claude_stores_is_counted_once(tmp_path, repo_path):
    projects = tmp_path / "projects" / "-home-apiad-Workspace"
    projects.mkdir(parents=True)
    archive = tmp_path / "claude-import"
    archive.mkdir()
    line = _claude_record(repo_path)
    (projects / "s1.jsonl").write_text(line + "\n")
    with gzip.open(archive / "s1.jsonl.gz", "wt") as fh:
        fh.write(line + "\n")

    scanner = _scanner(repo_path)
    scanner.run([(projects, "claude", "c"), (archive, "extra", "x")], None)

    assert _calls(scanner) == 1
    assert scanner.seen == {"msg_shared"}


def test_a_message_in_claudes_files_and_aegis_store_is_counted_once(
    tmp_path, repo_path
):
    """aegis stores the same lines Claude Code writes to ~/.claude/projects;
    each message is priced once, from Claude's file, which is read first."""
    projects = tmp_path / "projects" / "-home-apiad-Workspace"
    projects.mkdir(parents=True)
    (projects / "s1.jsonl").write_text(_claude_record(repo_path) + "\n")
    state = tmp_path / ".aegis" / "state"
    _store(
        state,
        "20260601-120000-aaaaaa",
        repo_path,
        [("2026-06-01T12:00:00Z", _assistant("msg_shared"))],
    )

    scanner = _scanner(repo_path)
    scanner.run([(projects, "claude", "c")], state)

    assert _calls(scanner) == 1
    sources = {s.source for s in scanner.sessions.values() if s.usage}
    assert sources == {"claude"}


def test_a_line_without_a_message_id_is_not_counted(tmp_path, repo_path):
    """An imported legacy tool call repeats its message's usage without the
    message's id. Counting it charged the message once more per tool call:
    on repos/rift since 2026-09-20, 14% more calls and 22% more cache reads
    than the legacy engine, all of them from aegis's store."""
    state = tmp_path / ".aegis" / "state"
    _store(
        state,
        "20260601-120000-aaaaaa",
        repo_path,
        [
            ("2026-06-01T12:00:00Z", _assistant("msg_one")),
            ("2026-06-01T12:00:01Z", _assistant(None)),
            ("2026-06-01T12:00:02Z", _assistant(None)),
        ],
    )

    scanner = _scanner(repo_path)
    scanner.run([], state, foreign=False)

    assert _calls(scanner) == 1


def test_lines_of_one_message_are_counted_once(tmp_path, repo_path):
    """Claude prints one line per content block, each repeating the usage."""
    state = tmp_path / ".aegis" / "state"
    _store(
        state,
        "20260601-120000-aaaaaa",
        repo_path,
        [
            ("2026-06-01T12:00:00Z", _assistant("msg_one")),
            ("2026-06-01T12:00:01Z", _assistant("msg_one")),
        ],
    )

    scanner = _scanner(repo_path)
    scanner.run([], state, foreign=False)

    assert _calls(scanner) == 1


def test_no_foreign_skips_the_home_projects_store(tmp_path, repo_path):
    projects = tmp_path / "projects" / "-home-apiad-Workspace"
    projects.mkdir(parents=True)
    (projects / "s1.jsonl").write_text(_claude_record(repo_path) + "\n")

    scanner = _scanner(repo_path)
    scanner.run([(projects, "claude", "c")], None, foreign=False)

    assert scanner.sessions == {}
    assert scanner.first_seen is None


def test_a_truncated_json_line_does_not_stop_the_scan(tmp_path, repo_path):
    projects = tmp_path / "projects" / "-home-apiad-Workspace"
    projects.mkdir(parents=True)
    (projects / "s1.jsonl").write_text(
        '{"timestamp": "2026-06-01T11:0\n' + _claude_record(repo_path) + "\n"
    )

    scanner = _scanner(repo_path)
    scanner.run([(projects, "claude", "c")], None, foreign=True)

    assert len(scanner.seen) == 1


def test_a_truncated_gzip_archive_does_not_take_the_command_down(tmp_path, repo_path):
    """gzip only finds truncation while decompressing, inside the read loop,
    and raises EOFError, which is not an OSError: guarding only open() lets an
    archive cut short abort the whole measurement with a traceback."""
    archive = tmp_path / "claude-import"
    archive.mkdir()
    path = archive / "s1.jsonl.gz"
    with gzip.open(path, "wt") as fh:
        for i in range(200):
            fh.write(_claude_record(repo_path, f"msg_{i}") + "\n")
    whole = path.read_bytes()
    path.write_bytes(whole[: len(whole) // 2])

    scanner = _scanner(repo_path)
    scanner.run([(archive, "extra", "x")], None, foreign=False)  # must not raise

    # Whatever decompressed before the truncation is kept.
    assert len(scanner.seen) > 0


def test_a_window_that_cuts_the_session_start_keeps_its_cwd_and_model(
    tmp_path, repo_path
):
    """The spawn record holds the cwd and Claude's init line the model; both
    sit at the session's start. Filtering by --since before reading them drops
    the cwd (and with it the session's share) and reprices at the default."""
    state = tmp_path / ".aegis" / "state"
    usage_free_line = {"type": "assistant", "message": {"id": "msg_one", "content": []}}
    usage_free_line["message"]["usage"] = {"input_tokens": 1_000_000}
    _store(
        state,
        "20260806-235000-aaaaaa",
        repo_path,
        [
            (
                "2026-08-06T23:50:00Z",
                {"type": "system", "subtype": "init", "model": "claude-haiku-4-5"},
            ),
            ("2026-08-07T00:10:00Z", usage_free_line),
        ],
    )

    scanner = _scanner(repo_path, since="2026-08-07")
    scanner.run([], state, foreign=False)

    [session] = scanner.sessions.values()
    assert session.cwd == str(repo_path)
    haiku = prices_for("claude-haiku-4-5")
    assert haiku is not None
    assert _cost(scanner) == pytest.approx(float(haiku.input), rel=1e-9)


def test_an_unknown_model_is_counted_as_unpriced_not_charged(tmp_path, repo_path):
    state = tmp_path / ".aegis" / "state"
    _store(
        state,
        "20260601-120000-aaaaaa",
        repo_path,
        [("2026-06-01T12:00:00Z", _assistant("msg_one", model="claude-newest-9"))],
    )

    scanner = _scanner(repo_path)
    scanner.run([], state, foreign=False)

    [session] = scanner.sessions.values()
    assert session.unpriced["calls"] == 1
    assert session.unpriced["tokens"] == 10 + 20 + 100
    assert _cost(scanner) == 0


def test_writes_without_the_cache_split_take_the_measured_one_hour_share(
    tmp_path, repo_path
):
    """Imported legacy lines report only a total of cache writes. They are split
    by the 1-hour share of the lines that reported it, not priced all at the
    5-minute rate."""
    state = tmp_path / ".aegis" / "state"
    split = {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 300}
    _store(
        state,
        "20260601-120000-aaaaaa",
        repo_path,
        [
            (
                "2026-06-01T12:00:00Z",
                _assistant(
                    "msg_split", cache_creation_input_tokens=400, cache_creation=split
                ),
            ),
            (
                "2026-06-01T12:00:01Z",
                _assistant("msg_total", cache_creation_input_tokens=1000),
            ),
        ],
    )

    scanner = _scanner(repo_path)
    scanner.run([], state, foreign=False)

    [session] = scanner.sessions.values()
    [bucket] = session.usage.values()
    assert bucket["cc5"] == 100 + 250
    assert bucket["cc1"] == 300 + 750


def test_an_opencode_step_counts_once_as_unpriced_work(tmp_path, repo_path):
    """OpenCode reports a step's tokens on its step-finish part, which can
    arrive more than once; no OpenCode model has a price here."""
    state = tmp_path / ".aegis" / "state"
    step = opencode_step("prt_1", inp=500, out=100, read=5000)
    opencode_store(
        state, "20260602-130000-oooooo", repo_path, "2026-06-02T13:05:00Z", step, step
    )

    scanner = _scanner(repo_path)
    scanner.run([], state, foreign=False)

    [session] = scanner.sessions.values()
    assert session.unpriced["calls"] == 1
    assert session.unpriced["tokens"] == 500 + 100 + 5000
    assert _cost(scanner) == 0


def test_codex_requests_are_priced_by_their_turns_model(tmp_path, repo_path):
    """Each usage line is one request, priced with the model of the aegis/turn
    line before it: an OpenAI model at its rates, a :free model at zero, and a
    model with no price counted as unpriced work."""
    from aegis.usage.prices import codex_prices_for

    state = tmp_path / ".aegis" / "state"
    codex_store(
        state, "20260602-140000-cccccc", repo_path, "2026-06-02T14:05:00Z",
        codex_turn("openai/gpt-6.1-sol"),
        codex_usage("u1", inp=1_000_000),
        codex_usage("u1", inp=1_000_000),
        codex_turn("openrouter/nvidia/x:free"),
        codex_usage("u2", inp=1_000_000),
        codex_turn("openai/gpt-test"),
        codex_usage("u3", inp=700, out=300),
    )  # fmt: skip

    scanner = _scanner(repo_path)
    scanner.run([], state, foreign=False)

    [session] = scanner.sessions.values()
    assert _calls(scanner) == 4
    assert _cost(scanner) == pytest.approx(
        2 * float(codex_prices_for("openai/gpt-6.1-sol").input)
    )
    assert session.unpriced["calls"] == 1 and session.unpriced["tokens"] == 1000
