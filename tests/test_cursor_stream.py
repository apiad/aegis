"""The recorded turn (``fixtures/cursor/acp-turn.jsonl``) through the parser.

The capture is raw stdout of one real turn on cursor-agent 2026.10.14: two
responses, the command list, a title, one thought, 23 message chunks, two
tool calls (an Edit that completes with a diff, a ``cat`` still in progress),
and the permission request the driver answers rather than stores its own
way. The test wraps it in the own lines a session writes around any turn,
completes the call the capture never reached the way cursor-agent's
``presentCompletedToolCall`` would (``rawOutput``, not content), and folds
the store back.
"""

import json
from pathlib import Path

from aegis.cursor.stream import Parser
from aegis.transcript.entries import fold_records

FIXTURE = Path(__file__).parent / "fixtures" / "cursor" / "acp-turn.jsonl"
SESSION_ID = "64dcc856-8ac5-4d4c-8781-72c61b008d22"  # the capture's own session


def lines() -> list[str]:
    return FIXTURE.read_text().splitlines()


def updates():
    """Every ``session/update`` the capture carries, as its update dict."""
    for line in lines():
        update = (json.loads(line).get("params") or {}).get("update")
        if isinstance(update, dict):
            yield update


def records() -> list[dict]:
    """The turn as the store keeps it: aegis's own lines around the capture,
    and the completion of ``cat note2.txt``, which the capture never reached
    (it ends mid-turn), as cursor-agent sends a shell's result."""
    call = json.loads(lines()[31])["params"]["update"]
    done = json.dumps(
        {
            "method": "session/update",
            "params": {
                "sessionId": SESSION_ID,
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": call["toolCallId"],
                    "status": "completed",
                    "rawOutput": {"exitCode": 0, "stdout": "Y\n", "stderr": ""},
                },
            },
        }
    )
    wrapped = [
        json.dumps(
            {
                "method": "aegis/session",
                "params": {"session_id": SESSION_ID, "model": "fake-pro", "cwd": "/ws"},
            }
        ),
        json.dumps(
            {
                "method": "aegis/echo",
                "params": {"text": "create note2.txt with Y and read it back"},
            }
        ),
        *lines(),
        done,
        json.dumps(
            {
                "method": "aegis/turn_done",
                "params": {"stop_reason": "end_turn", "duration_ms": 4210},
            }
        ),
    ]
    return [
        {"i": i, "ts": 1000.0 + i, "src": "cursor", "line": line}
        for i, line in enumerate(wrapped)
    ]


def test_the_recorded_turn_folds_back_into_entries():
    entries = fold_records(records()).entries()
    assert any(e["summary"] == "Cursor Agent · fake-pro" for e in entries)
    assert [e["md"] for e in entries if e["kind"] == "user"] == [
        "create note2.txt with Y and read it back"
    ]

    def text_of(kind: str) -> str:
        parts = "".join(
            str(u.get("content", {}).get("text") or "")
            for u in updates()
            if u.get("sessionUpdate") == kind
        )
        return parts

    # One row per block, holding every chunk: the store has no closing update
    # to rebuild from, so each chunk line's own text-so-far is the record.
    thinking = [e for e in entries if e["kind"] == "thinking"]
    prose = [e for e in entries if e["kind"] == "prose"]
    assert [e["md"] for e in thinking] == [text_of("agent_thought_chunk")]
    assert [e["md"] for e in prose] == [text_of("agent_message_chunk")]
    assert prose[0]["status"] == "ok"

    tools = [e for e in entries if e["kind"] == "tool"]
    assert len(tools) == 2
    edit, cat = tools
    assert edit["status"] == "ok" and cat["status"] == "ok"
    # the Edit's row ends with the file and its diff (the completion's content)
    assert edit["detail"]["path"] == "/tmp/opencode/cursor-probe/ws/note2.txt"
    assert edit["detail"]["diff"]["path"].endswith("note2.txt")
    # the cat's row carries the stdout that arrived in rawOutput
    assert "Y" in (cat["detail"].get("result") or "")

    assert any(e["summary"] == "done in 4.2s" for e in entries)


def test_the_lines_a_driver_intercepts_or_drops_draw_nothing():
    p = Parser()
    fixture = lines()
    # 1 and 2 are answers (process.py resolves them into futures), 34 is the
    # permission request it answers; all three would fold to an entry if the
    # parser read them as updates.
    for index in (0, 1, 33):
        events = p.feed(fixture[index])
        assert [type(ev).__name__ for ev in events] == ["Ignored"]
    # the command list reaches the catalog through process.py, not the fold
    assert p.feed(fixture[2]) == []


def test_each_chunk_line_carries_the_message_so_far():
    p = Parser()
    seen: list[str] = []
    for update in updates():
        if update.get("sessionUpdate") != "agent_message_chunk":
            continue
        (event,) = p.feed(
            json.dumps(
                {
                    "method": "session/update",
                    "params": {"sessionId": SESSION_ID, "update": update},
                }
            )
        )
        seen.append(event.text)
    # a growing prefix: line n carries chunks 1..n, so the last line alone
    # holds the whole message (the store keeps these lines for that reason)
    assert all(b.startswith(a) for a, b in zip(seen, seen[1:]))
    assert seen[-1] == "".join(
        str(u.get("content", {}).get("text") or "")
        for u in updates()
        if u.get("sessionUpdate") == "agent_message_chunk"
    )
