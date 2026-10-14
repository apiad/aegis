"""/btw's window and prompt, from fold entries."""

from aegis import btw
from aegis.transcript.entries import EMPTY_STANDING


def user(k, text):
    return {"id": f"u{k}", "kind": "user", "md": text, "status": "ok", "detail": {}}


def prose(k, text):
    return {"id": f"p{k}", "kind": "prose", "md": text, "status": "ok", "detail": {}}


def tool(k, title="Bash", summary="ls", result="ok", status="ok"):
    return {
        "id": f"t{k}",
        "kind": "tool",
        "title": title,
        "summary": summary,
        "status": status,
        "md": None,
        "detail": {"result": result},
    }


def note(k, q, a, status="ok"):
    return {
        "id": f"b{k}",
        "kind": "btw",
        "status": status,
        "md": None,
        "detail": {"question": q, "answer": a},
    }


def test_user_prose_and_tools_render_in_order():
    w = btw.window([user(1, "go"), prose(2, "on it"), tool(3, result="2 files")])
    assert w.text.splitlines() == [
        "user: go",
        "assistant: on it",
        "tool: Bash(ls) -> 2 files",
    ]
    assert w.header == "all 1 turn"


def test_an_error_tool_is_marked_and_other_kinds_are_left_out():
    es = [
        user(1, "go"),
        tool(2, status="err", result="Exit code 1"),
        {"id": "x", "kind": "thinking", "md": "hm", "status": "ok", "detail": {}},
        note(3, "q", "a"),
    ]
    assert btw.window(es).text.splitlines() == [
        "user: go",
        "tool[error]: Bash(ls) -> Exit code 1",
    ]


def test_the_turn_cap_keeps_the_last_turns_and_says_so():
    es = []
    for k in range(15):
        es += [user(k, f"ask {k}"), prose(k, f"reply {k}")]
    w = btw.window(es, max_turns=10)
    assert "ask 4" not in w.text and "ask 5" in w.text and "ask 14" in w.text
    assert w.header == "last 10 of 15 turns" and w.bound_by == "turns"


def test_the_users_line_outranks_the_newest_tool_calls():
    es = [user(0, "the question that matters")] + [
        tool(k, result="x" * 400) for k in range(200)
    ]
    w = btw.window(es, budget_tokens=2_000)
    assert "user: the question that matters" in w.text
    assert w.bound_by == "budget"


def test_prose_is_capped_at_its_share_so_tool_calls_keep_a_seat():
    es = (
        [user(0, "go")]
        + [prose(k, "p" * 2_000) for k in range(10)]
        + [tool(99, result="kept")]
    )
    w = btw.window(es, budget_tokens=2_000)
    assert "-> kept" in w.text


def test_reordering_the_fill_keeps_transcript_order():
    es = [user(0, "a"), tool(1, result="b"), prose(2, "c"), tool(3, result="d")]
    lines = btw.window(es).text.splitlines()
    assert [ln[-1] for ln in lines] == ["a", "b", "c", "d"]


def test_long_items_are_clipped_and_counted():
    w = btw.window([user(0, "go"), tool(1, result="r" * 5_000)], item_chars=500)
    assert "[+4,500 chars]" in w.text and w.header == "all 1 turn · 1 item truncated"


def test_one_item_larger_than_the_budget_still_gives_a_window():
    w = btw.window([user(0, "u" * 50_000)], budget_tokens=1_000)
    assert w.text.startswith("user: uuu") and len(w.text) <= 4_100


def test_an_empty_transcript_gives_an_empty_window():
    w = btw.window([])
    assert w.text == "" and w.turns_total == 0


def test_earlier_keeps_answered_notes_only():
    es = [note(1, "q1", "a1"), note(2, "q2", "", status="failed"), note(3, "q3", "a3")]
    assert btw.earlier(es) == [("q1", "a1"), ("q3", "a3")]


def test_the_prompt_puts_the_question_last_after_plan_report_and_history():
    st = {
        **EMPTY_STANDING,
        "report": {"attention": "review", "line": "Read the diff", "replies": []},
        "plan": [{"text": "land it", "state": "doing"}],
    }
    w = btw.window([user(0, "go")])
    hist = [(f"q{k}", f"a{k}") for k in range(8)]
    p = btw.prompt("why sqlite?", w, st, hist)
    assert p.rstrip().endswith("The person's side question: why sqlite?")
    assert "AGENT REPORT (review): Read the diff" in p and "[doing] land it" in p
    assert "Q: q2" not in p and "Q: q3" in p and "Q: q7" in p  # the last five
    assert "all 1 turn" in p


def test_peer_read_never_shows_a_side_note():
    from aegis.agent_ops import _render

    e = {
        **note(1, "the secret side question", "a"),
        "summary": "the secret side question",
    }
    assert _render(e, True) is None
