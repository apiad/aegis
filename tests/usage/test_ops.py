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


def turn(rec, person, cost, mode="person", billing="api"):
    return TurnRow(rec, NOW - 3600, person, "foreground", "claude-code", "claude-opus-5-5", mode,
                   billing, Decimal(cost), "billed", 0, 0, 0, 0, 10.0, 2, 1, 1, 0, 0, None)  # fmt: skip


def part(rec, repo, cost):
    return PartRow(rec, 0, NOW - 3600, repo, "build", "editing", True, Decimal(cost), 100, 5.0)  # fmt: skip


def stand_in(tmp_path, multi_user=None, name="here", billing="api", readings=()):
    tmp_path.mkdir(exist_ok=True)
    c, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(c)
    tables.insert(c, "s1", Derived(turns=[turn(1, "ana", "0.6", billing=billing)],
                                   parts=[part(1, "/w/atlas", "0.6")]))  # fmt: skip
    tables.insert(c, "s2", Derived(turns=[turn(1, "ben", "1", "queue", billing)],
                                   parts=[part(1, "/w/notes", "1")]))  # fmt: skip
    c.executemany("INSERT INTO usage_quota VALUES(?, 'weekly_all', ?, ?, ?)",
                  [(billing, *r) for r in readings])  # fmt: skip
    c.commit()
    c.close()
    app = SimpleNamespace(
        registry=Registry(),
        journal=SimpleNamespace(path=tmp_path / "journal.db", usage_progress=dict),
        config=SimpleNamespace(
            current=lambda: SimpleNamespace(
                usage={"owners": {}, "subscriptions": {"a1b2": {"fee": 200}}},
                context={"handoff_at": 300_000},
            )
        ),
        server_name=name,
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
                      "unpriced_tokens", "backfill", "subs", "sub_hours", "context",
                      "servers"}  # fmt: skip
    assert a["servers"] == {"here": {"at": a["at"], "age_s": 0, "state": "live"}}


async def test_an_agent_is_answered_for_this_server_alone(app):
    a = await app.registry.call("usage.read", {}, Caller("agent", log_id="x"))
    assert "servers" not in a and a["totals"]["cost"] == 1.6


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


class FakeLink:
    """A linked server: another stand-in, called as a link calls."""

    def __init__(self, far, state="linked"):
        self.far, self.name, self.state = far, far.server_name, state
        self.error: str | None = None
        self.asked: list[dict] = []

    async def call(self, op, params=None):
        self.asked.append(params)
        if self.error:
            raise OpError(self.error, "no")
        return await self.far.registry.call(op, params, Caller("user", link="here"))


class FakeLinks:
    def __init__(self, *links):
        self._links = links

    def up(self):
        return [link for link in self._links if link.state == "linked"]

    def links(self):
        return list(self._links)


def linked(tmp_path, *names, **kw):
    app = stand_in(tmp_path / "here", **kw)
    links = [FakeLink(stand_in(tmp_path / n, name=n)) for n in names]
    app.links = FakeLinks(*links)
    return app, links


async def test_rows_from_every_linked_server_carry_their_server(tmp_path):
    app, [beta] = linked(tmp_path, "beta")
    a = await app.registry.call("usage.read", {"split": ["repo"]})
    assert sorted((r["server"], r["repo"]) for r in a["rows"]) == [
        ("beta", "/w/atlas"), ("beta", "/w/notes"),
        ("here", "/w/atlas"), ("here", "/w/notes")]  # fmt: skip
    assert a["totals"]["cost"] == 3.2
    assert {n: s["state"] for n, s in a["servers"].items()} == {
        "here": "live",
        "beta": "live",
    }
    assert beta.asked[0]["server"] == "beta" and beta.asked[0]["split"] == ["repo"]


async def test_a_server_that_fails_serves_its_last_answer_as_cached(tmp_path):
    app, [beta] = linked(tmp_path, "beta")
    first = await app.registry.call("usage.read", {})
    beta.error = "server_offline"
    a = await app.registry.call("usage.read", {})
    s = a["servers"]["beta"]
    assert s["state"] == "cached" and s["at"] == first["servers"]["beta"]["at"]
    assert isinstance(s["age_s"], int) and a["totals"]["cost"] == 3.2


async def test_a_server_never_answered_is_missing(tmp_path):
    app, [beta] = linked(tmp_path, "beta")
    beta.state = "offline"
    a = await app.registry.call("usage.read", {})
    assert a["servers"]["beta"] == {"at": None, "age_s": None, "state": "missing"}
    assert a["totals"]["cost"] == 1.6 and not beta.asked


async def test_an_older_aegis_without_usage_read_is_missing(tmp_path):
    app, [beta] = linked(tmp_path, "beta")
    beta.error = "unknown_op"
    a = await app.registry.call("usage.read", {"split": ["repo"]})
    assert a["servers"]["beta"]["state"] == "missing"
    assert {r["server"] for r in a["rows"]} == {"here"}
    assert a["totals"]["cost"] == 1.6


async def test_a_members_call_asks_no_link(tmp_path):
    app, [beta] = linked(tmp_path, "beta", multi_user=object())
    a = await app.registry.call("usage.read", {}, BEN)
    assert not beta.asked and set(a["servers"]) == {"here"}
    assert a["totals"]["cost"] == 1.0


async def test_a_subscription_two_servers_share_counts_once(tmp_path):
    """Both servers run on claude:a1b2 in the same hour; only this one reads
    the quota, 10% then 14%. The 4 points are shared once over both servers'
    costs, not once per server."""
    app = stand_in(tmp_path / "here", billing="claude:a1b2",
                   readings=[(NOW - 5400, 10.0, NOW + 86400),
                             (NOW - 1800, 14.0, NOW + 86400)])  # fmt: skip
    beta = FakeLink(stand_in(tmp_path / "beta", name="beta", billing="claude:a1b2"))
    app.links = FakeLinks(beta)
    a = await app.registry.call("usage.read", {"split": ["owner"]})
    week = 200 * 12 / 52
    by = {}
    for r in a["rows"]:
        by[r["server"]] = by.get(r["server"], 0) + r["limit"]
    assert by == pytest.approx({"here": 2.0, "beta": 2.0})
    assert a["totals"]["limit"] == pytest.approx(4.0)
    assert a["totals"]["sub"] == pytest.approx(week * 4 / 100)
    s = a["subs"]["claude:a1b2"]
    assert s["kind"] == "metered" and s["elsewhere"] == 0 and s["value"] == 3.2


async def test_the_page_merges_linked_servers(tmp_path):
    app, [beta] = linked(tmp_path, "beta")
    p = await app.registry.call("usage.page", {"split": "server"})
    assert {n: s["state"] for n, s in p["servers"].items()} == {
        "here": "live",
        "beta": "live",
    }
    assert [c["name"] for c in p["controls"]["servers"]] == ["here", "beta"]
    assert "3.20" in p["tiles"][0]["value"]
    p = await app.registry.call("usage.page", {"servers": ["beta"]})
    assert "1.60" in p["tiles"][0]["value"]
