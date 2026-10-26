"""The page's panels from hand-built answers, and usage.page on the app's
stand-in."""

from datetime import datetime, timezone

from aegis.usage.page import panels, window

from .test_ops import stand_in

MONDAYS = ["2026-10-12", "2026-10-19", "2026-10-26"]


def row(**kw):
    base = {"cost": 0.0, "tokens": 0, "secs": 0.0, "sub": 0.0, "waits": 0.0}
    return base | kw


def ans(rows=(), totals=None, outcomes=(), estimated=0.0, unpriced=0):
    totals = totals or row(cost=sum(r["cost"] for r in rows))
    return {"rows": list(rows), "totals": totals, "outcomes": list(outcomes),
            "estimated": estimated, "unpriced_tokens": unpriced, "backfill": None}  # fmt: skip


def server(cost=10.0, prev=8.0, owners=(("acme-co", 6.0), ("ana", 4.0))):
    return {
        "total": ans(totals=row(cost=cost, secs=7200), estimated=0.25,
                     unpriced=1_500_000, outcomes=[{"prs_merged": 4}]),
        "prev": ans(totals=row(cost=prev)),
        "trend": ans([row(purpose="build", bucket="2026-10-12", cost=3.0),
                      row(purpose="fix", bucket="2026-10-26", cost=2.0),
                      row(purpose="build", bucket="2026-10-26", cost=5.0)]),
        "owners": ans([row(owner=o, cost=c) for o, c in owners]),
        "heat": ans([row(repo="/w/atlas", purpose="build", cost=3.0),
                     row(repo="/w/atlas", purpose="chat", cost=1.0),
                     row(repo="/w/notes", purpose="docs", cost=2.0)]),
        "when": ans([row(bucket="Mon 09", cost=4.0), row(bucket="Sun 23", cost=1.0)]),
        "mode": ans([row(mode="person", cost=4.0, waits=3600),
                     row(mode="queue", cost=6.0)]),
        "model": ans([row(harness="claude-code", model="opus", cost=9.0),
                      row(harness="codex", model="gpt", tokens=2000)]),
        "outcomes": ans([row(repo="/w/atlas", cost=8.0)],
                        outcomes=[{"repo": "/w/atlas", "commits": 5, "prs_opened": 2,
                                   "prs_merged": 2, "issues": 1}]),
    }  # fmt: skip


def one(**kw):
    args = {"window_weeks": 3, "split": "purpose", "measure": "cost", "names": {},
            "mondays": MONDAYS} | kw  # fmt: skip
    return panels(args.pop("answers", {"here": server()}), **args)


def test_the_five_tiles_and_the_change_on_the_previous_period():
    tiles = {t["key"]: t for t in one()["tiles"]}
    assert list(tiles) == ["total", "hours", "alone", "per_pr", "subs"]
    assert tiles["total"]["value"] == "$10.00"
    assert tiles["total"]["change"] == {"text": "+25%", "dir": "up"}
    assert (
        tiles["hours"]["value"] == "2.0 h" and "1.0 h waiting" in tiles["hours"]["sub"]
    )
    assert tiles["alone"]["value"] == "60%"
    assert tiles["per_pr"]["value"] == "$2.50"


def test_heat_rows_are_repos_and_columns_purposes_without_chat():
    heat = one()["heat"]
    assert heat["cols"] == ["build", "docs"]
    atlas, notes = heat["rows"]
    assert atlas["label"] == "atlas" and notes["label"] == "notes"
    assert [c["share"] for c in atlas["cells"]] == [0.75, 0]  # chat counts in the whole
    assert atlas["cells"][0]["where"] == {"repo": "/w/atlas", "purpose": "build"}
    assert [c["text"] for c in notes["cells"]] == ["", "100%"]


def test_owners_are_sorted_by_total_and_split_by_server():
    answers = {"here": server(), "far": server(owners=(("ana", 5.0),))}
    owners = one(answers=answers)["owners"]
    assert [o["owner"] for o in owners] == ["ana", "acme-co"]
    assert [(p["server"], p["text"]) for p in owners[0]["parts"]] == [
        ("here", "$4.00"),
        ("far", "$5.00"),
    ]
    assert owners[0]["frac"] == 1.0 and owners[1]["frac"] == 6 / 9


def test_owner_names_relabel_owners():
    owners = one(names={"acme-co": "Acme"})["owners"]
    assert [o["label"] for o in owners] == ["Acme", "ana"]
    assert owners[0]["where"] == {"owner": "acme-co"}


def test_the_trend_has_a_stack_per_week_and_a_series_per_key():
    trend = one()["trend"]
    assert [w["monday"] for w in trend["weeks"]] == MONDAYS
    assert [len(w["stack"]) for w in trend["weeks"]] == [1, 0, 2]
    assert [(s["key"], s["colour"]) for s in trend["series"]] == [
        ("build", 0),
        ("fix", 1),
    ]
    assert trend["weeks"][2]["frac"] == 1.0 and trend["weeks"][0]["frac"] == 3 / 7
    assert trend["title"] == "Spend per week"


def test_the_foot_names_the_share_estimated_and_unpriced():
    foot = one()["foot"]
    assert foot["estimated"] == "25%" and foot["unpriced"] == "1.5M"
    assert "25% of cost estimated" in foot["lines"][0]


def test_unpriced_models_show_tokens_and_the_measure_moves_every_panel():
    model = one()["model"]
    assert [m["text"] for m in model] == ["$9.00", "2.0k tokens, unpriced"]
    p = one(measure="hours")
    assert (
        p["tiles"][0]["value"] == "2.0 h"
        and p["trend"]["title"] == "Agent hours per week"
    )


def test_the_window_is_whole_weeks_ending_now():
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc).timestamp()  # a Wednesday
    start, prev, prev_end, mondays = window(3, now, "UTC")
    assert mondays == MONDAYS
    assert start == datetime(2026, 10, 12, tzinfo=timezone.utc).timestamp()
    assert prev == start - 3 * 7 * 86400 and prev_end == now - 3 * 7 * 86400


async def test_the_page_op_answers_every_panel(tmp_path):
    app = stand_in(tmp_path)
    p = await app.registry.call("usage.page", {"window": 2, "tz": "UTC"})
    assert set(p) >= {"tiles", "trend", "owners", "heat", "when", "mode", "model",
                      "outcomes", "foot", "controls"}  # fmt: skip
    assert p["tiles"][0]["value"] == "$1.60" and len(p["trend"]["weeks"]) == 2
    assert [m["key"] for m in p["mode"]] == ["queue", "person"]


SUB = {"name": "claude-max", "fee": 100.0, "kind": "metered", "sub": 60.0,
       "elsewhere": 20.0, "unused": 20.0, "elsewhere_usd": 20.0, "unused_usd": 20.0,
       "apportioned": 15.0, "value": 250.0}  # fmt: skip
CONTEXT = {
    "bands": [{"band": 0, "calls": 300, "cost": 10.0, "tok_cr": 0},
              {"band": 3, "calls": 100, "cost": 30.0, "tok_cr": 0}],
    "peaks": [[40_000, False], [250_000, False], [350_000, True]],
    "replay": [{"at": at, "handoffs": h, "saved": s, "rewrite": 0.5 if h else 0.0, "reread": r}
               for at, h, s, r in ((100_000, 3, 9.0, 1.0), (150_000, 2, 5.0, 1.0),
                                   (200_000, 2, 4.0, 1.0), (300_000, 1, 1.0, 1.0),
                                   (400_000, 0, 0.0, 1.0))],
}  # fmt: skip


def with_subs_and_context():
    s = server()
    s["subs"] = ans([row(billing="claude:a1b2", owner="acme-co", sub=40.0, limit=40.0),
                     row(billing="claude:a1b2", owner="ana", sub=20.0, limit=20.0)])
    s["subs"]["subs"] = {"claude:a1b2": SUB}
    s["context"] = ans([row(context=0, cost=10.0)])
    s["context"]["context"] = CONTEXT
    return {"here": s}


def test_a_subscription_panel_splits_its_fee_by_owner_elsewhere_and_unused():
    (p,) = one(answers=with_subs_and_context())["subscriptions"]
    assert (p["name"], p["fee"], p["ratio"]) == ("claude-max", "$100", "2.5×")
    assert [(x["label"], x["text"]) for x in p["parts"]] == [
        ("acme-co", "$40.00"), ("ana", "$20.00"), ("elsewhere", "$20.00"),
        ("unused", "$20.00")]  # fmt: skip
    assert round(sum(x["frac"] for x in p["parts"]), 9) == 1.0
    assert "25% of it apportioned" in p["note"]


def test_the_context_panel_has_bands_peaks_and_every_threshold():
    c = one(answers=with_subs_and_context(), handoff_at=300_000)["context"]
    b = {x["label"]: x for x in c["bands"]}
    assert (b["0–50k"]["calls"], b["0–50k"]["cost"]) == ("75%", "25%")
    assert b["200k–300k"]["per_call"] == "$0.30" and b["0–50k"]["per_call"] == "$0.03"
    assert [p["label"] for p in c["peaks"]["rows"]][-1] == "600k+"
    marked = [p["label"] for p in c["peaks"]["rows"] if p["mark"]]
    assert marked == ["300k–400k"]
    handed = {p["label"]: (p["handed"], p["other"]) for p in c["peaks"]["rows"]}
    assert handed["300k–400k"] == (1, 0) and handed["200k–300k"] == (0, 1)
    r = {x["at"]: x for x in c["replay"]}
    assert list(r) == ["100k", "150k", "200k", "300k", "400k"]
    assert r["100k"]["net"] == "$5.50"  # 9 − 0.5 − 3 × 1
    assert r["400k"]["net"] == "$0.00" and r["400k"]["handoffs"] == 0


def test_the_measure_control_offers_sub_and_old_answers_draw_empty_panels():
    p = one()
    assert p["subscriptions"] == [] and p["context"]["bands"] == []


async def test_the_page_op_answers_the_new_panels(tmp_path):
    app = stand_in(tmp_path)
    p = await app.registry.call("usage.page", {"window": 2, "tz": "UTC"})
    assert "sub" in [m["key"] for m in p["controls"]["measures"]]
    assert {"subscriptions", "context"} <= set(p)
    assert [x["at"] for x in p["context"]["replay"]][0] == "100k"
