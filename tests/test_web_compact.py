from aegis.state.event_codec import decode_event, encode_event
from aegis.events import AssistantText, AssistantThinking, ToolResult, ToolUse
from aegis.web.compact import compact_encoded


def test_tool_result_over_head_is_clipped():
    body = "\n".join(f"line{i}" for i in range(50))
    d = encode_event(ToolResult(text=body, is_error=False))
    out, truncated = compact_encoded(d)
    assert truncated is True
    assert out["text"].count("\n") < body.count("\n")
    assert out["full_len"] == len(body)
    decode_event(out)  # still a valid event dict


def test_short_tool_result_untouched():
    d = encode_event(ToolResult(text="one\ntwo", is_error=False))
    out, truncated = compact_encoded(d)
    assert truncated is False and out == d


def test_tool_use_drops_raw_input():
    d = encode_event(ToolUse(name="Bash", summary="ls",
                             raw_input={"command": "ls -la /very/long"}))
    out, truncated = compact_encoded(d)
    assert truncated is True and "raw_input" not in out
    assert out["name"] == "Bash" and out["summary"] == "ls"


def test_thinking_body_emptied_with_len():
    d = encode_event(AssistantThinking(text="a long private thought",
                                       usage=None))
    out, truncated = compact_encoded(d)
    assert truncated is True and out["text"] == ""
    assert out["full_len"] == len("a long private thought")


def test_assistant_text_passes_through():
    d = encode_event(AssistantText(text="the answer", usage=None))
    out, truncated = compact_encoded(d)
    assert truncated is False and out == d
