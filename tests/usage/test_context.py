"""Context bands and the handoff replay (spec 09)."""

from decimal import Decimal

from aegis.journal import db
from aegis.usage import tables
from aegis.usage.context import THRESHOLDS, Replay, band, reread_cost
from aegis.usage.prices import prices_for
from aegis.usage.turns import Derived, PartRow, SessionInfo, TurnRow

from .records import UseRec, run


def sum_at(out, at):
    rows = [o[at] for o in out if at in o]
    return (sum(r[0] for r in rows), sum((r[1] for r in rows), Decimal(0)),
            sum((r[2] for r in rows), Decimal(0)))  # fmt: skip


def test_bands():
    assert [band(x) for x in (0, 49_999, 50_000, 250_000, 900_000)] == [0, 0, 1, 3, 6]


def test_a_replay_matches_a_hand_computed_saving():
    p = prices_for("claude-opus-5-5")
    assert p.cache_read == Decimal("0.20")  # per million
    r = Replay(start=29_000)
    out = [r.call(c, p) for c in (90_000, 120_000, 150_000)]
    h, saved, rewrite = sum_at(
        out, 100_000
    )  # one handoff at 120k: offset 91k on 2 calls
    assert h == 1 and saved == Decimal(2 * 91_000) * p.cache_read / 1_000_000
    assert rewrite == Decimal(29_000) * p.cache_write_5m / 1_000_000
    assert sum_at(out, 150_000) == (0, 0, 0)  # 150k is not passed: 150k is not above it


def test_a_threshold_above_the_peak_saves_nothing():
    r = Replay(start=30_000)
    out = [r.call(c, prices_for("claude-opus-5-5")) for c in (100_000, 390_000)]
    assert sum_at(out, 400_000) == (0, 0, 0)
    assert sum_at(out, 300_000)[0] == 1


def test_a_long_session_hands_off_again_after_the_successor_grows():
    r = Replay(start=30_000)
    p = prices_for("claude-opus-5-5")
    out = [r.call(c, p) for c in (110_000, 150_000, 200_000)]
    # 110k: hand off (offset 80k); 150k is 70k; 200k is 120k: hand off again
    assert sum_at(out, 100_000)[0] == 2


def _ctx(rec_ctx):
    r = UseRec()
    r.spawn()
    for k, ctx in enumerate(rec_ctx):
        r.own("send", text=f"t{k}")
        r.assistant(f"m{k}a", inp=10, cr=ctx - 10, text="ok")
        r.assistant(f"m{k}b", inp=10, cr=ctx + 60_000 - 10, text="ok")
        r.result(cost_total=0.1 * (k + 1))
    return r


def test_the_deriver_writes_bands_and_whatifs_per_turn():
    d = run(_ctx([40_000, 140_000]), SessionInfo(continues="s0"))
    t1, t2 = d.turns
    assert (t1.ctx_start, t1.ctx_peak, t1.continues) == (40_000, 100_000, "s0")
    assert t2.continues is None
    bands = {(b.rec, b.band): b.calls for b in d.bands}
    assert bands == {(t1.rec, 0): 1, (t1.rec, 2): 1, (t2.rec, 2): 1, (t2.rec, 3): 1}
    assert sum(b.cost for b in d.bands if b.rec == t1.rec) == t1.cost
    at = {(w.rec, w.at): w.handoffs for w in d.whatifs}
    assert at[(t2.rec, 100_000)] == 1 and (t1.rec, 100_000) not in at
    assert {w.at for w in d.whatifs} == {100_000, 150_000}
    assert all(w.at in THRESHOLDS for w in d.whatifs)


def _turn(rec, continues=None, ctx_start=0):
    return TurnRow(rec, 1000.0 + rec, "ana", "foreground", "claude-code", "m", "person",
                   "api", Decimal(1), "billed", 0, 0, 0, 0, 1.0, 1, 0, 0, ctx_start,
                   ctx_start, continues)  # fmt: skip


def _reading(rec, cost):
    return PartRow(rec, 0, 1000.0 + rec, None, "build", "reading", True,
                   Decimal(cost), 1, 1.0)  # fmt: skip


def test_reread_cost_is_the_median_of_successors_first_five_turns_reading(tmp_path):
    con, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(con)
    for log, costs in (("a", ["0.1"] * 7), ("b", ["0.3", "0.3"]), ("c", ["0.5"])):
        tables.insert(con, log, Derived(
            turns=[_turn(k, "p" if k == 0 else None) for k in range(len(costs))],
            parts=[_reading(k, c) for k, c in enumerate(costs)]))  # fmt: skip
    tables.insert(con, "x", Derived(turns=[_turn(0)], parts=[_reading(0, "9")]))
    assert reread_cost(con) == Decimal("0.5")  # 0.5, 0.6, 0.5: not x, no successor


def test_the_whatif_measure_is_net_of_rereading_and_shared_by_part_cost(tmp_path):
    from aegis.usage.query import UsageQuery, run
    from aegis.usage.turns import WhatIfRow

    con, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(con)

    def part(n, repo, cost, activity="editing"):
        return PartRow(
            1, n, 1001.0, repo, "build", activity, True, Decimal(cost), 1, 1.0
        )

    tables.insert(con, "s1", Derived(
        turns=[_turn(1)], parts=[part(0, "/w/atlas", "0.75"), part(1, "/w/notes", "0.25")],
        whatifs=[WhatIfRow(1, 100_000, 1, Decimal("0.5"), Decimal("0.1"))]))  # fmt: skip
    tables.insert(con, "s2", Derived(turns=[_turn(1, "s1")],
                                     parts=[part(0, None, "0.2", "reading")]))  # fmt: skip
    a = run(con, UsageQuery(since=0, until=2000, split=("repo",), whatif=100_000))
    by = {r["repo"]: r["whatif"] for r in a.rows}
    assert by == {"/w/atlas": 0.15, "/w/notes": 0.05, None: 0.0}
    assert round(a.totals["whatif"], 9) == 0.2
    assert (
        run(con, UsageQuery(since=0, until=2000, whatif=400_000)).totals["whatif"] == 0
    )
