import json

from aegis2.claude.stream import (
    Compact,
    Echo,
    Garbled,
    Ignored,
    Init,
    Notice,
    Result,
    Text,
    Thinking,
    ToolCall,
    ToolOutput,
    Usage,
    parse,
)

USAGE = {
    "input_tokens": 10,
    "cache_creation_input_tokens": 9234,
    "cache_read_input_tokens": 13696,
    "output_tokens": 4,
}


def line(obj) -> str:
    return json.dumps(obj)


def test_init():
    (ev,) = parse(
        line(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "s1",
                "model": "claude-haiku-4-5-20251001",
                "claude_code_version": "2.1.283",
            }
        )
    )
    assert ev == Init(
        session_id="s1", model="claude-haiku-4-5-20251001", version="2.1.283"
    )


def test_assistant_line_with_several_blocks_yields_all_of_them():
    evs = parse(
        line(
            {
                "type": "assistant",
                "message": {
                    "usage": USAGE,
                    "content": [
                        {"type": "thinking", "thinking": "hmm"},
                        {"type": "text", "text": "Hello"},
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        },
                    ],
                },
            }
        )
    )
    u = Usage(input=10, cache_creation=9234, cache_read=13696, output=4)
    assert evs == [
        Thinking(text="hmm", parent=None, usage=u),
        Text(text="Hello", parent=None, usage=u),
        ToolCall(id="t1", name="Bash", input={"command": "ls"}, parent=None, usage=u),
    ]
    assert u.context == 10 + 9234 + 13696 + 4


def test_subagent_events_carry_their_parent():
    (ev,) = parse(
        line(
            {
                "type": "assistant",
                "parent_tool_use_id": "task1",
                "message": {"content": [{"type": "text", "text": "inner"}]},
            }
        )
    )
    assert ev.parent == "task1"


def test_tool_result_with_list_content():
    (ev,) = parse(
        line(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "is_error": True,
                            "content": [{"type": "text", "text": "Exit code 1"}],
                        }
                    ]
                },
            }
        )
    )
    assert ev == ToolOutput(id="t1", text="Exit code 1", is_error=True, parent=None)


def test_echo_is_a_replayed_user_message():
    (ev,) = parse(
        line(
            {
                "type": "user",
                "isReplay": True,
                "message": {"role": "user", "content": "hi there"},
            }
        )
    )
    assert ev == Echo(text="hi there")


def test_a_user_line_that_is_not_a_replay_is_ignored():
    (ev,) = parse(
        line({"type": "user", "message": {"role": "user", "content": "skill body"}})
    )
    assert isinstance(ev, Ignored)


def test_result_reads_cost_and_context_window():
    (ev,) = parse(
        line(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "duration_ms": 1530,
                "total_cost_usd": 0.0201726,
                "stop_reason": "end_turn",
                "modelUsage": {"claude-haiku-4-5-20251001": {"contextWindow": 200000}},
            }
        )
    )
    assert ev == Result(
        is_error=False,
        subtype="success",
        duration_ms=1530,
        cost_usd=0.0201726,
        stop_reason="end_turn",
        context_window=200000,
    )


def test_system_notices():
    for sub in (
        "thinking_tokens",
        "hook_started",
        "hook_response",
        "task_started",
        "task_notification",
    ):
        assert parse(line({"type": "system", "subtype": sub})) == [Notice(subtype=sub)]


def test_compact_boundary():
    (ev,) = parse(
        line(
            {
                "type": "system",
                "subtype": "compact_boundary",
                "compact_metadata": {"pre_tokens": 150000, "post_tokens": 30000},
            }
        )
    )
    assert ev == Compact(pre_tokens=150000, post_tokens=30000)


def test_unhandled_types_are_ignored_and_garbage_is_garbled():
    assert parse(line({"type": "rate_limit_event"})) == [
        Ignored(type="rate_limit_event")
    ]
    assert parse("not json") == [Garbled(raw="not json")]
    assert parse("[1, 2]") == [Garbled(raw="[1, 2]")]
    assert parse(line({"type": "assistant", "message": {"content": []}})) == [
        Ignored(type="assistant")
    ]
