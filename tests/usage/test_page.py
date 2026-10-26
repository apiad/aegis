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
