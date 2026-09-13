from aegis.bench.script import (
    acp_chunks, inject_marker, make_script, route, synthetic_blocks,
    synthetic_fill)


def test_route_uses_first_word_then_default_then_ack():
    s = make_script({"go": synthetic_blocks(2, 0)},
                    default=synthetic_blocks(1, 0))
    assert len(route(s, "Go now")) == 2
    assert len(route(s, "other")) == 1
    assert len(route(make_script({}), "x")) == 1


def test_inject_marker_into_text_and_stream_delta():
    line = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hello"}]}}
    assert inject_marker(line, "«b0001»")
    assert line["message"]["content"][0]["text"] == "«b0001» hello"
    ev = {"type": "stream_event", "event": {"type": "content_block_delta",
          "delta": {"type": "text_delta", "text": "abc"}}}
    assert inject_marker(ev, "«b0002»")
    assert ev["event"]["delta"]["text"] == "abc «b0002» "
    tool = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t", "name": "Read", "input": {}}]}}
    assert not inject_marker(tool, "«b0003»")


def test_fill_is_unmarked_and_alternates_kinds():
    steps = synthetic_fill(3)
    assert all(s["mark"] is False for s in steps)
    kinds = [s["line"]["type"] for s in steps]
    assert kinds == ["assistant", "assistant", "user"] * 3


def test_acp_chunks_marks_every_nth():
    steps = acp_chunks(10, 50, mark_every=5)
    assert [s["mark"] for s in steps].count(True) == 2
    assert steps[1]["dt_ms"] == 20.0
