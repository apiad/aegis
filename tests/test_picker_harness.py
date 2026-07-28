from aegis.config.harnesses import merge_harnesses
from aegis.tui.picker import build_picker_rows, resolve_transient_agent


HN = merge_harnesses({})


def test_rows_presets_then_harnesses():
    rows = build_picker_rows(["opus", "quick"], HN)
    ids = [r[0] for r in rows]
    assert ids[:2] == ["opus", "quick"]
    assert "harness:opencode" in ids


def test_resolve_transient_agent():
    a = resolve_transient_agent(
        "opencode", "opencode/mimo-v2.5-free", None, HN)
    assert a.harness == "opencode"
    assert a.model == "opencode/mimo-v2.5-free"


def test_resolve_transient_agent_with_effort():
    a = resolve_transient_agent("claude-code", "opus", "low", HN)
    assert a.harness == "claude-code"
    assert a.effort.value == "low"
