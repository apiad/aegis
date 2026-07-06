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


def test_event_frame_is_compact_but_keeps_html():
    from aegis.web.subscriptions import event_frame
    body = "\n".join(f"row{i}" for i in range(40))
    fr = event_frame("h", 5, ToolResult(text=body, is_error=False))
    assert fr["kind"] == "event" and fr["seq"] == 5
    assert fr["truncated"] is True
    assert fr["event"]["text"].count("\n") < body.count("\n")   # compacted
    assert "row0" in fr["html"]                                  # full in html
