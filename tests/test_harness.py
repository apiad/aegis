import pytest

from aegis.claude.harness import ClaudeCode
from aegis.harness import harness_for
from aegis.transcript.entries import Fold


def test_harness_for_picks_by_name():
    h = harness_for("claude-code", "/bin/claude", "/bin/opencode")
    assert isinstance(h, ClaudeCode)
    assert (h.name, h.src, h.label, h.tool_prefix, h.bin) == (
        "claude-code",
        "claude",
        "Claude Code",
        "mcp__aegis__",
        "/bin/claude",
    )


def test_an_unknown_harness_is_refused():
    with pytest.raises(ValueError, match="lovelaice"):
        harness_for("lovelaice", "claude", "opencode")


def test_the_fold_parses_by_src_and_keeps_one_parser_per_src():
    f = Fold()
    line = '{"type":"system","subtype":"init","session_id":"s","model":"m"}'
    (ev,) = f.parse("claude", line)
    assert ev.session_id == "s"
    assert f.parse("claude", "not json")[0].raw == "not json"


def test_the_primer_names_tools_by_the_harness_prefix(tmp_path):
    from aegis.mcp import primer
    from aegis.meta import MetaStore
    from aegis.session import Session, SpawnSpec
    from aegis.transcript.store import Store

    def make(harness):
        return Session(
            log_id="p",
            spec=SpawnSpec("a", "m", "high", "full", tmp_path, harness=harness),
            handle="quiet-owl",
            store=Store(tmp_path / f"{harness}.jsonl"),
            stderr_path=tmp_path / "e",
            claude_bin="claude",
            publish=lambda ch, ops: None,
            metas=MetaStore(tmp_path / "sessions"),
        )

    assert "(mcp__aegis__*)" in primer(make("claude-code"), "zion")
