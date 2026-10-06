import json

from aegis.events import parse, ToolUse, AssistantText, ToolResult


def _line(obj):
    return json.dumps(obj)


def test_assistant_tool_use_carries_parent_id():
    line = _line({
        "type": "assistant",
        "parent_tool_use_id": "toolu_PARENT",
        "message": {"id": "msg_1", "content": [
            {"type": "tool_use", "id": "toolu_child", "name": "Read",
             "input": {"file_path": "x.py"}}]},
    })
    ev = parse(line)
    assert isinstance(ev, ToolUse)
    assert ev.parent_tool_use_id == "toolu_PARENT"


def test_assistant_text_parent_absent_is_none():
    line = _line({
        "type": "assistant",
        "message": {"id": "m", "content": [{"type": "text", "text": "hi"}]},
    })
    ev = parse(line)
    assert isinstance(ev, AssistantText)
    assert ev.parent_tool_use_id is None


def test_tool_result_carries_parent_id():
    line = _line({
        "type": "user",
        "parent_tool_use_id": "toolu_PARENT",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_child",
             "content": "ok", "is_error": False}]},
    })
    ev = parse(line)
    assert isinstance(ev, ToolResult)
    assert ev.parent_tool_use_id == "toolu_PARENT"
