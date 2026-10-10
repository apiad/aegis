"""scripts/smoke.py's reading of a transcript (the know-how on the smoke test)."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "smoke.py"


def load():
    spec = importlib.util.spec_from_file_location("smoke", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_tour_has_asked_only_once_aegis_records_a_turn_end(tmp_path):
    """The tour's prompt tells the agent to call turn_end, so the word is in the
    transcript from the first line. Only aegis's turn_end record means the agent
    has asked; a check on the word passed in 0 s on 2026-10-09 (#245)."""
    smoke = load()
    t = tmp_path / "t.jsonl"
    lines = [{"src": "aegis", "kind": "send", "text": "Ask Alex, then call turn_end."}]
    t.write_text("".join(json.dumps(r) + "\n" for r in lines))
    assert not smoke.asked(t)
    lines.append(
        {"src": "aegis", "kind": "turn_end", "attention": "needs_you", "line": "Works?"}
    )
    t.write_text("".join(json.dumps(r) + "\n" for r in lines))
    assert smoke.asked(t)
    assert "  [turn_end needs_you] Works?" in smoke.conversation(t)


def test_a_record_whose_message_is_a_string_is_printed_not_a_crash(tmp_path):
    """Claude's permission_denied line carries `message` as a string. The reader
    took every `message` for a dict and crashed on the 2026-10-10 smoke test's
    Haiku transcript (#307)."""
    smoke = load()
    denied = {
        "type": "system",
        "subtype": "permission_denied",
        "tool_name": "mcp__aegis__turn_end",
        "message": "Cannot call mcp__aegis__turn_end while in plan mode.",
    }
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"src": "claude", "line": json.dumps(denied)}) + "\n")
    assert smoke.conversation(t) == [
        "  !! permission_denied mcp__aegis__turn_end: "
        "Cannot call mcp__aegis__turn_end while in plan mode."
    ]
