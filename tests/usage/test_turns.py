"""The usage deriver: a turn's cost, split over what it did (spec 06)."""

from decimal import Decimal

from aegis.usage.prices import prices_for

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
