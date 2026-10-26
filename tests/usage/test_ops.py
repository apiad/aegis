"""usage.read over hand-built rows, on a stand-in for the app."""

import functools
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aegis.app import App
from aegis.journal import db
from aegis.mcp import _schema
from aegis.ops import Caller, OpError, Principal, Registry
from aegis.usage import tables
from aegis.usage.ops import register_usage_ops
from aegis.usage.turns import Derived, PartRow, TurnRow

NOW = time.time()
ANA = Caller("user", principal=Principal("ana", "member"))
BEN = Caller("user", principal=Principal("ben", "member"))
ADMIN = Caller("user", principal=Principal("ana", "admin"))


def turn(rec, person, cost, mode="person"):
    return TurnRow(rec, NOW - 3600, person, "foreground", "claude-code", "claude-opus-5-5", mode,
                   "api", Decimal(cost), "billed", 0, 0, 0, 0, 10.0, 2, 1, 1, 0, 0, None)  # fmt: skip


def part(rec, repo, cost):
    return PartRow(rec, 0, NOW - 3600, repo, "build", "editing", True, Decimal(cost), 100, 5.0)  # fmt: skip


def stand_in(tmp_path, multi_user=None):
    c, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(c)
    tables.insert(c, "s1", Derived(turns=[turn(1, "ana", "0.6")],
                                   parts=[part(1, "/w/atlas", "0.6")]))  # fmt: skip
    tables.insert(c, "s2", Derived(turns=[turn(1, "ben", "1", "queue")],
                                   parts=[part(1, "/w/notes", "1")]))  # fmt: skip
    c.commit()
    c.close()
    app = SimpleNamespace(
        registry=Registry(),
        journal=SimpleNamespace(path=tmp_path / "journal.db", usage_progress=dict),
        config=SimpleNamespace(
            current=lambda: SimpleNamespace(
                usage={"owners": {}}, context={"handoff_at": 300_000}
            )
        ),
        server_name="here",
        multi_user=multi_user,
        user="ana",
        who=lambda c: c.principal or Principal("", "admin"),
    )
    app.sees = functools.partial(App.sees, app)
    register_usage_ops(app)
    return app


@pytest.fixture
def app(tmp_path):
    return stand_in(tmp_path)


async def test_the_op_answers_with_rows_and_totals(app):
    a = await app.registry.call("usage.read", {"split": ["repo"]})
    assert {r["repo"]: r["cost"] for r in a["rows"]} == {
        "/w/atlas": 0.6,
        "/w/notes": 1.0,
    }
    assert a["rows"][0]["repo"] == "/w/notes"  # sorted by the measure
    assert a["totals"]["cost"] == 1.6 and a["server"] == "here"
    assert set(a) == {"server", "at", "rows", "totals", "outcomes", "estimated",
                      "unpriced_tokens", "backfill", "subs", "sub_hours", "context"}  # fmt: skip


async def test_a_where_narrows_and_a_server_split_names_this_server(app):
    a = await app.registry.call(
        "usage.read", {"split": ["server", "repo"], "where": {"mode": "queue"}}
    )
    assert [(r["server"], r["repo"]) for r in a["rows"]] == [("here", "/w/notes")]


async def test_a_member_sees_only_its_own_sessions_and_an_admin_all(tmp_path):
    app = stand_in(tmp_path, multi_user=object())
    a = await app.registry.call("usage.read", {}, BEN)
    assert a["totals"]["cost"] == 1.0
    a = await app.registry.call("usage.read", {}, ANA)
    assert a["totals"]["cost"] == 0.6
    a = await app.registry.call("usage.read", {}, ADMIN)
    assert a["totals"]["cost"] == 1.6


async def test_a_call_over_a_link_answers_for_this_server_only(app):
    with pytest.raises(OpError) as e:
        await app.registry.call("usage.read", {"server": "far"})
    assert "here" in str(e.value)
    a = await app.registry.call(
        "usage.read", {"server": "far"}, Caller("user", link="far")
    )
    assert a["server"] == "here" and a["totals"]["cost"] == 1.6


@pytest.mark.parametrize(
    "params, allowed",
    [({"split": ["colour"]}, "purpose"), ({"measure": "joy"}, "tokens"),
     ({"where": {"colour": "x"}}, "purpose"), ({"by": "year"}, "week")],
)  # fmt: skip
async def test_an_unknown_split_or_measure_names_the_allowed_values(
    app, params, allowed
):
    with pytest.raises(OpError) as e:
        await app.registry.call("usage.read", params)
    assert e.value.code == "bad_params" and allowed in str(e.value)


async def test_since_takes_weeks_and_the_journals_forms(app):
    for since in ("1w", "7d", "2026-01-01"):
        a = await app.registry.call("usage.read", {"since": since})
        assert a["totals"]["cost"] == 1.6, since
    with pytest.raises(OpError):
        await app.registry.call("usage.read", {"since": "soon"})


def test_the_mcp_schema_lists_usage_read(app):
    ops = {op.tool_name: op for op in app.registry.agent_ops()}
    assert ops["usage_read"].read
    props = _schema(ops["usage_read"])["properties"]
    assert {
        "since",
        "until",
        "by",
        "split",
        "measure",
        "where",
        "server",
        "whatif",
    } <= set(props)
