from aegis.events import ToolUse, AssistantText
from aegis.state.event_codec import encode_event, decode_event


def test_parent_id_round_trips_when_set():
    ev = ToolUse(name="Read", summary="x", tool_call_id="c",
                 parent_tool_use_id="toolu_P")
    d = encode_event(ev)
    assert d["parent_tool_use_id"] == "toolu_P"
    assert decode_event(d).parent_tool_use_id == "toolu_P"


def test_parent_id_absent_when_none():
    d = encode_event(AssistantText(text="hi"))
    assert "parent_tool_use_id" not in d
    assert decode_event(d).parent_tool_use_id is None
