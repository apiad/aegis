"""Recording turns a real claude stream into a replayable fixture.

Only the pure half is tested here: which lines are kept, how delays are
carried, and that nothing personal reaches a committed file. The real
recording costs tokens and runs by hand (``aegis bench record``).
"""
from aegis.bench.record import sanitize, steps_from


def _text(t):
    return {"type": "assistant",
            "message": {"content": [{"type": "text", "text": t}]}}


def test_sanitize_drops_lines_the_fake_owns():
    assert sanitize({"type": "system", "subtype": "init"}, "/tmp/w") is None
    assert sanitize({"type": "result"}, "/tmp/w") is None


def test_sanitize_strips_ids_and_scrubs_work_and_home_paths():
    line = {"type": "user", "uuid": "u", "session_id": "s", "message": {
        "content": [{"type": "tool_result",
                     "content": "/tmp/w/notes.md and /home/alex/.claude ok"}]}}
    out = sanitize(line, "/tmp/w", home="/home/alex")
    assert "uuid" not in out and "session_id" not in out
    assert out["message"]["content"][0]["content"] == (
        "/work/notes.md and /home/user/.claude ok")


def test_steps_carry_dropped_delay_and_cap_it():
    timed = [(300.0, {"type": "system", "subtype": "init"}),
             (400.0, _text("hi")),
             (200.0, {"type": "system", "subtype": "thinking_tokens"}),
             (9000.0, _text("there")),
             (10.0, {"type": "result"})]
    steps = steps_from(timed, "/tmp/w")
    # The first kept step carries init's 300 ms plus its own 400 ms, under
    # the 1 s first-step cap; the second carries 200 + 9000, capped at 5 s.
    assert [s["dt_ms"] for s in steps] == [700.0, 5000.0]
    assert all(s["mark"] is True for s in steps)
    assert [s["line"]["message"]["content"][0]["text"] for s in steps] == [
        "hi", "there"]


def test_first_step_is_capped_at_one_second():
    steps = steps_from([(4000.0, _text("slow start"))], "/tmp/w")
    assert steps[0]["dt_ms"] == 1000.0
