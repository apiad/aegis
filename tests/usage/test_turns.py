"""The usage deriver: a turn's cost, split over what it did (spec 06)."""

from decimal import Decimal

import pytest

from aegis.usage.prices import prices_for
from aegis.usage.turns import SessionInfo, Settle, WaitRow

from .records import UseRec, run


def test_a_turn_with_two_messages_splits_its_billed_cost_by_weight():
    r = UseRec()
    r.spawn(cwd="/w/atlas")
    r.own("send", text="go")
    r.assistant(
        "m1", inp=1000, out=100, tools=[("a", "Read", {"file_path": "/w/atlas/x.py"})]
    )
    r.output("a", "...")
    r.assistant(
        "m2",
        inp=1000,
        out=1000,
        tools=[
            ("b", "Edit", {"file_path": "/w/atlas/x.py"}),
            ("c", "Bash", {"command": "uv run pytest"}),
        ],
    )
    r.output("b", "ok")
    r.output("c", "1 passed")
    r.result(cost_total=0.30)
    d = run(r)
    t = d.turns[0]
    assert t.cost == Decimal("0.30") and t.cost_kind == "billed" and t.calls == 3
    assert sum(p.cost for p in d.parts) == Decimal("0.30")
    by = {p.activity: p.cost for p in d.parts}
    assert by["reading"] < by["editing"] == by["testing"]
    assert sum(p.tokens for p in d.parts) == t.tok_in + t.tok_out + t.tok_cw + t.tok_cr


def test_text_only_messages_spread_over_the_tool_messages():
    r = UseRec()
    r.spawn()
    r.own("send", text="go")
    r.assistant("m1", inp=1000, tools=[("a", "Read", {"file_path": "/w/x.py"})])
    r.output("a", "...")
    r.assistant("m2", inp=1000, tools=[("b", "WebSearch", {"query": "q"})])
    r.output("b", "...")
    r.assistant("m3", inp=2000, text="done")
    r.result(cost_total=0.40)
    d = run(r)
    assert [p.cost for p in d.parts] == [Decimal("0.20"), Decimal("0.20")]


def test_a_turn_without_tools_is_one_talking_part():
    r = UseRec()
    r.spawn()
    r.own("send", text="hello")
    r.assistant("m1", inp=500, out=50, text="hi")
    r.result(cost_total=0.02)
    d = run(r)
    assert len(d.parts) == 1
    p = d.parts[0]
    assert (p.activity, p.purpose, p.settled, p.cost) == (
        "talking",
        "chat",
        True,
        Decimal("0.02"),
    )


def test_a_resume_restarts_the_running_total():  # review focus 1
    r = UseRec()
    r.spawn()
    for k, total in enumerate((0.50, 0.80, 0.10)):
        r.own("send", text=f"turn {k}")
        r.assistant(f"m{k}", inp=100, text="ok")
        r.result(cost_total=total)
    d = run(r)
    assert [t.cost for t in d.turns] == [
        Decimal("0.50"),
        Decimal("0.30"),
        Decimal("0.10"),
    ]


def test_an_interrupted_turn_counts_its_tokens_as_priced():  # review focus 2
    r = UseRec()
    r.spawn()
    r.own("send", text="go")
    r.assistant(
        "m1", inp=1000, out=200, cr=5000, tools=[("a", "Read", {"file_path": "/w/x"})]
    )
    r.own("stop")
    d = run(r)
    assert len(d.turns) == 1
    t = d.turns[0]
    want = prices_for("claude-opus-5-5").cost(
        inp=1000, out=200, cc5=0, cc1=0, cache_read=5000
    )
    assert t.cost_kind == "priced" and t.cost == want
    assert sum(p.cost for p in d.parts) == want


def test_the_result_after_an_interrupt_is_net_of_the_priced_turn():
    r = UseRec()
    r.spawn()
    r.own("send", text="go")
    r.assistant("m1", inp=1000, text="ok")
    r.result(cost_total=0.10)
    r.own("send", text="more")
    r.assistant("m2", inp=10_000, tools=[("a", "Read", {"file_path": "/w/x"})])
    r.own("stop")
    r.own("send", text="again")
    r.assistant("m3", inp=1000, text="ok")
    r.result(cost_total=0.20)
    d = run(r)
    priced = prices_for("claude-opus-5-5").cost(
        inp=10_000, out=0, cc5=0, cc1=0, cache_read=0
    )
    assert d.turns[1].cost == priced
    assert d.turns[2].cost == Decimal("0.10") - priced


def test_a_message_repeated_on_three_lines_counts_once():  # review focus 3
    r = UseRec()
    r.spawn()
    r.own("send", text="go")
    r.assistant(
        "m1",
        inp=700,
        out=30,
        tools=[(k, "Read", {"file_path": f"/w/{k}"}) for k in "abc"],
    )
    for k in "abc":
        r.output(k, "...")
    r.result(cost_total=0.05)
    d = run(r)
    t = d.turns[0]
    assert (t.tok_in, t.tok_out, t.calls) == (700, 30, 3)


def _turn(r, text, *calls, cost=None):
    """A turn woken by ``text`` making ``calls`` (id, tool, input, output);
    returns the record index of its result."""
    r.own("send", text=text)
    for k, (cid, tool, inp, out) in enumerate(calls):
        r.assistant(f"{cid}-m", inp=100 * (k + 1), tools=[(cid, tool, inp)])
        r.output(cid, out)
    r.assistant(f"{text}-end", inp=10, text="done")
    return r.result(cost_total=cost)["i"]


def test_a_code_edit_is_settled_by_the_next_commit_in_its_repo():
    r = UseRec()
    r.spawn(cwd="/w/atlas")
    t1 = _turn(r, "edit", ("a", "Edit", {"file_path": "/w/atlas/a.py"}, "ok"))
    _turn(
        r,
        "commit",
        ("b", "Bash", {"command": "git commit -qm 'fix: y'"}, "[main 1a2b3c4] fix: y"),
    )
    d = run(r, repo_of={"/w/atlas": "atlas"}.get)
    first = [p for p in d.parts if p.rec == t1][0]
    assert (first.repo, first.purpose, first.settled) == ("atlas", "build", False)
    assert Settle(rec=t1, n=0, purpose="fix") in d.settles
    commit = [p for p in d.parts if p.activity == "committing"][0]
    assert (commit.purpose, commit.settled) == ("fix", True)


def test_a_commit_in_another_repo_leaves_the_edit_open():
    r = UseRec()
    r.spawn(cwd="/w")
    t1 = _turn(r, "edit", ("a", "Edit", {"file_path": "/w/atlas/a.py"}, "ok"))
    _turn(
        r,
        "commit",
        (
            "b",
            "Bash",
            {"command": "git -C /w/notes commit -qm 'docs: y'"},
            "[main 1a2b3c4] docs: y",
        ),
    )
    d = run(r, repo_of={"/w/atlas": "atlas", "/w/notes": "notes"}.get)
    assert not [s for s in d.settles if s.rec == t1]


def test_reading_takes_the_purpose_of_the_next_purposeful_call():
    r = UseRec()
    r.spawn()
    t1 = _turn(r, "read", ("a", "Read", {"file_path": "/w/x.py"}, "..."))
    _turn(r, "search", ("b", "WebSearch", {"query": "q"}, "..."))
    d = run(r)
    assert Settle(rec=t1, n=0, purpose="research") in d.settles


def test_reading_in_the_same_turn_settles_at_once():
    r = UseRec()
    r.spawn()
    _turn(
        r,
        "go",
        ("a", "Read", {"file_path": "/w/x.py"}, "..."),
        ("b", "WebSearch", {"query": "q"}, "..."),
    )
    d = run(r)
    assert {(p.activity, p.purpose, p.settled) for p in d.parts} == {
        ("reading", "research", True),
        ("web", "research", True),
    }


def test_support_at_the_end_takes_the_last_purpose_and_background_without_one_is_review():
    r = UseRec()
    r.spawn()
    _turn(r, "search", ("a", "WebSearch", {"query": "q"}, "..."))
    t2 = _turn(r, "read", ("b", "Read", {"file_path": "/w/x.py"}, "..."))
    r.own("close")
    d = run(r)
    part = [p for p in d.parts if p.rec == t2][0]
    assert (part.purpose, part.settled) == ("research", False)
    assert Settle(rec=t2, n=0, purpose="research") in d.settles

    r = UseRec()
    r.spawn()
    t1 = _turn(r, "review this", ("a", "Read", {"file_path": "/w/x.py"}, "..."))
    r.own("close")
    d = run(r, info=SessionInfo(kind="worker"))
    assert d.parts[0].purpose == "review"
    assert Settle(rec=t1, n=0, purpose="review") in d.settles


def test_a_pr_read_is_review_unless_the_turn_edits_after_it():
    r = UseRec()
    r.spawn()
    _turn(r, "look", ("a", "Bash", {"command": "gh pr diff 3"}, "..."))
    _turn(
        r,
        "fix",
        ("b", "Bash", {"command": "gh pr view 3"}, "..."),
        ("c", "Edit", {"file_path": "/w/a.py"}, "ok"),
    )
    d = run(r)
    assert d.parts[0].purpose == "review" and d.parts[0].settled
    view = [p for p in d.parts if p.rec != d.parts[0].rec and p.activity == "reading"]
    assert not view[0].settled


def test_repo_by_path_then_workdir_then_gh_repo_then_last_then_cwd():
    repo_of = {
        "/w/atlas": "atlas",
        "/w/notes": "notes",
        "gh:acme-co/ledger": "ledger",
    }.get
    r = UseRec()
    r.spawn(cwd="/w")
    _turn(r, "1", ("a", "Read", {"file_path": "/w/atlas/x"}, "..."))
    _turn(r, "2", ("b", "Bash", {"command": "cd /w/notes && ls"}, "..."))
    _turn(r, "3", ("c", "Bash", {"command": "gh pr view 3 --repo acme-co/ledger"}, "."))
    _turn(r, "4", ("d", "ToolSearch", {"query": "x"}, "..."))
    _turn(r, "5", ("e", "Read", {"file_path": "/tmp/x"}, "..."))
    d = run(r, repo_of=repo_of)
    assert [p.repo for p in d.parts] == ["atlas", "notes", "ledger", "ledger", "ledger"]

    r = UseRec()
    r.spawn(cwd="/w/atlas")
    _turn(r, "1", ("a", "ToolSearch", {"query": "x"}, "..."))
    assert run(r, repo_of=repo_of).parts[0].repo == "atlas"


@pytest.mark.parametrize(
    "text,kind,first,want",
    [
        ("do it", "foreground", False, "person"),
        ("> from agent:calm-hopper · x", "foreground", False, "agent"),
        ("> from queue:general · task#3 · ok", "foreground", False, "queue"),
        ("> from monitor:m1 · done", "foreground", False, "monitor"),
        ("> from artifact:a1 · submit", "foreground", False, "artifact"),
        ("> from schedule:nightly · run#2", "foreground", False, "schedule"),
        ("> from workflow:w1 · done", "foreground", False, "workflow"),
        ("> from feedback · x", "foreground", False, "other"),
        ("review this", "worker", True, "queue"),
        ("build x", "workflow", True, "workflow"),
        ("do it", "foreground", True, "person"),
    ],
)
def test_mode(text, kind, first, want):
    r = UseRec()
    r.spawn()
    if not first:
        _turn(r, "hello")
    _turn(r, text)
    assert run(r, info=SessionInfo(kind=kind)).turns[-1].mode == want


def test_an_idle_gap_is_a_wait_capped_at_8_hours():
    r = UseRec()
    r.spawn()
    _turn(r, "go")
    r.wait(40_000)
    send = r.own("send", text="> from monitor:m1 · done")
    r.assistant("m9", inp=10, text="ok")
    r.result()
    d = run(r)
    assert d.waits == [
        WaitRow(rec=send["i"], ts=send["ts"], woke_by="monitor", secs=28_800)
    ]
