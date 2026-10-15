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

    assert "(mcp__aegis__*)" in primer(make("claude-code"), "zion", tmp_path)
    assert "(aegis_*)" in primer(make("opencode"), "zion", tmp_path)


def test_the_primer_asks_for_a_language_on_every_fence():
    """highlight.js scores bash and python equal on short shell; a label
    removes the guess (#323)."""
    from aegis.mcp import PRIMER

    assert "Label every code fence with its language" in PRIMER


def test_the_primer_prefers_aegis_tools_over_the_harness():
    """A harness's own waits, todo list and subagents never reach the person's
    screen (#387)."""
    from aegis.mcp import PRIMER

    rule = PRIMER.split("Where aegis and your harness both offer a way")[1]
    rule = rule.split("\n\n")[0]
    for tool in ("monitor_start", "plan_update", "queue_enqueue", "session_spawn"):
        assert tool in rule
    assert "one self-contained HTML page" in PRIMER


def test_opencode_is_a_harness():
    from aegis.opencode.harness import OpenCode

    h = harness_for("opencode", "claude", "/bin/opencode")
    assert isinstance(h, OpenCode)
    assert (h.name, h.src, h.label, h.tool_prefix, h.bin) == (
        "opencode",
        "opencode",
        "OpenCode",
        "aegis_",
        "/bin/opencode",
    )


def test_the_primer_names_the_root_paths_are_quoted_from(tmp_path):
    from types import SimpleNamespace

    from aegis.mcp import primer

    s = SimpleNamespace(
        handle="ana-b", harness=SimpleNamespace(tool_prefix="mcp__aegis__")
    )
    text = primer(s, "lab", tmp_path)
    assert (
        f"Quote a file as a path relative to {tmp_path}, or as an absolute path" in text
    )


def test_the_primer_says_when_to_reach_for_a_workflow():
    """Spec §10: agents choose a workflow without being told."""
    from aegis.mcp import PRIMER

    para = next(p for p in PRIMER.split("\n\n") if "workflow_arm" in p)
    for word in (
        "plan_update",
        "workflow_arm",
        "help_read",
        "approves",
        "many agents",
        "the workflow is the plan",
        "full permission",
        "commands (sh steps or probes)",
    ):
        assert word in para
