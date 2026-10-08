import pytest

from aegis.attention import card
from aegis.transcript.entries import EMPTY_STANDING


def st(**kw):
    return {**EMPTY_STANDING, **kw}


def rep(attention, line="a line", replies=()):
    return {"attention": attention, "line": line, "replies": list(replies)}


def a(standing, working=False, worker=False, waits=()):
    return card(standing, working=working, worker=worker, waits=list(waits))


@pytest.mark.parametrize(
    "standing, kw, want",
    [
        (st(report=rep("needs_you"), turn_error="boom"), {"working": True}, "working"),
        (st(report=rep("needs_you"), turn_error="boom"), {}, "error"),
        (st(report=rep("needs_you")), {"waits": ["1 monitor"]}, "needs_you"),
        (st(report=rep("review")), {"waits": ["1 monitor"]}, "waiting"),
        (st(report=rep("review")), {}, "review"),
        (st(report=rep("done")), {}, "done"),
        (st(), {}, "done"),
        (st(report=rep("needs_you")), {"worker": True}, "done"),
    ],
)
def test_the_first_rule_that_holds_wins(standing, kw, want):
    assert a(standing, **kw)["attention"] == want


def test_the_line_and_replies_follow_the_report():
    c = a(st(report=rep("needs_you", "Rebase or merge?", ["rebase", "merge"])))
    assert c["attention_line"] == "Rebase or merge?"
    assert c["replies"] == ["rebase", "merge"]
    assert a(st(report=rep("done", "Shipped", ["thanks"])))["attention_line"] == ""
    assert a(st(report=rep("done", "Shipped", ["thanks"])))["replies"] == ["thanks"]
    err = a(st(report=rep("needs_you", "q", ["x"]), turn_error="claude exited with code 3"))
    assert err["attention_line"] == "claude exited with code 3"
    assert err["replies"] == []
    assert a(st(report=rep("needs_you", "q", ["x"])), worker=True)["replies"] == []
    assert a(st(report=rep("needs_you", "q", ["x"])), working=True)["replies"] == []


def test_waiting_names_what_it_waits_on():
    c = a(st(), waits=["1 monitor", "2 background tasks"])
    assert c["waiting_on"] == "1 monitor, 2 background tasks"
    assert a(st(), working=True, waits=["1 monitor"])["waiting_on"] == ""


def test_the_plan_gives_now_did_and_counts():
    plan = [
        {"text": "read", "state": "done"},
        {"text": "fix", "state": "doing"},
        {"text": "ship", "state": "pending"},
    ]
    c = a(st(plan=plan, did="read"))
    assert (c["plan_now"], c["plan_did"], c["plan_done"], c["plan_total"]) == ("fix", "read", 1, 3)
    assert c["plan"] == plan
