"""Trusted links (#684): two servers one person alone uses, reaching each other
both ways over the one socket the near server opened (links.py, Backlink).

Alpha links beta with trust, as in test_links.py. Beta grants it while only its
admin holds a token, and its agents then reach alpha as ``handle@alpha``.
"""

import json
import re

import pytest
from typer.testing import CliRunner

from aegis import links
from aegis.cli import app as cli

from .conftest import until
from .test_links import Browser, Node, inbox, mcp, turn


async def _granted(alpha: Node, beta: Node) -> None:
    await until(
        lambda: (
            (link := alpha.app.links.get("beta")) is not None
            and link.state == "linked"
            and link.granted
            and beta.app.links.get("alpha") is not None
        ),
        timeout=10,
        what="the trusted link",
    )


@pytest.fixture
async def trusted(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    alpha.app.links.add("beta", beta.base, beta.token, trusted=True)
    await _granted(alpha, beta)
    yield alpha, beta
    await alpha.stop()
    await beta.stop()


# -- granting -----------------------------------------------------------------------
async def test_a_trusted_link_is_granted_and_opens_the_way_back(trusted):
    alpha, beta = trusted
    (w,) = alpha.app.links.wire()
    assert w["trusted"] and w["granted"] and w["trust_error"] == ""
    assert alpha.app.links.trusted("beta") and beta.app.links.trusted("alpha")
    incoming = await beta.app.registry.call("link.incoming", {})
    assert [(i["name"], i["state"]) for i in incoming] == [("alpha", "linked")]
    # The way back is not a link beta holds: its browsers do not list it.
    assert beta.app.links.wire() == []


async def test_a_plain_link_opens_no_way_back(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    try:
        alpha.app.links.add("beta", beta.base, beta.token)
        await until(lambda: alpha.app.links.get("beta").state == "linked")
        link = alpha.app.links.get("beta")
        assert not link.trusted and not link.granted
        assert beta.app.links.get("alpha") is None
        # An rcall down a plain link is dropped: nothing here answers it.
        link._receive(json.dumps({"t": "rcall", "id": 1, "op": "session.list"}))
        assert not link._answering
    finally:
        await alpha.stop()
        await beta.stop()


async def test_a_server_with_another_token_refuses_trust(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    try:
        beta.app.principals.add("ben", "member")
        # Asked through Settings, the refusal comes back with beta's reason.
        with pytest.raises(Exception) as e:
            await alpha.app.registry.call(
                "link.add", {"url": beta.base, "token": beta.token, "trusted": True}
            )
        assert "tokens besides its admin's" in str(e.value)
        assert alpha.app.links.wire() == []
        # A link already asking comes up plain, and says why.
        alpha.app.links.add("beta", beta.base, beta.token, trusted=True)
        await until(lambda: alpha.app.links.get("beta").state == "linked")
        link = alpha.app.links.get("beta")
        assert not link.granted and "tokens besides its admin's" in link.trust_error
        assert beta.app.links.get("alpha") is None
    finally:
        await alpha.stop()
        await beta.stop()


async def test_a_token_that_is_not_the_admins_gets_no_trust(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    try:
        token = beta.app.principals.add("alpha", "admin")
        with pytest.raises(links.LinkError) as e:
            await links.probe(beta.base, token, "alpha", "u", trusted=True)
        assert e.value.state == "trust_refused"
        assert "admin token" in e.value.message
    finally:
        await alpha.stop()
        await beta.stop()


async def test_a_token_minted_later_ends_the_trust_at_the_next_call(trusted):
    alpha, beta = trusted
    here = await alpha.spawn()
    far = await beta.spawn()
    beta.app.principals.add("ben", "member")
    out = await turn(
        far, mcp("peer_handoff", target=f"{here.handle}@alpha", context="hi")
    )
    assert out.startswith("mcp error:") and "trust_ended" in out, out
    await until(lambda: not alpha.app.links.get("beta").granted, what="trust ended")
    assert "tokens besides" in alpha.app.links.get("beta").trust_error
    assert beta.app.links.get("alpha") is None and not inbox(here)


async def test_link_trust_asks_and_stops_asking(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    try:
        alpha.app.links.add("beta", beta.base, beta.token)
        await until(lambda: alpha.app.links.get("beta").state == "linked")
        await alpha.app.registry.call("link.trust", {"name": "beta", "trusted": True})
        await _granted(alpha, beta)
        await alpha.app.registry.call("link.trust", {"name": "beta", "trusted": False})
        await until(
            lambda: (
                (link := alpha.app.links.get("beta")) is not None
                and link.state == "linked"
                and not link.trusted
                and beta.app.links.get("alpha") is None
            ),
            timeout=10,
            what="the link back to one way",
        )
    finally:
        await alpha.stop()
        await beta.stop()


# -- the way back ---------------------------------------------------------------------
async def test_an_agent_on_beta_hands_off_to_alpha(trusted):
    alpha, beta = trusted
    here, far = await alpha.spawn(), await beta.spawn()
    out = await turn(
        far,
        mcp("peer_handoff", target=f"{here.handle}@alpha", context="the fix is in"),
    )
    assert out.startswith("mcp ok:") and f"{here.handle}@alpha" in out, out
    await until(lambda: inbox(here), timeout=8, what="the delivery on alpha")
    (msg,) = inbox(here)
    assert f"from agent:{far.handle}@beta ({beta.app.user})" in msg["md"]
    assert "the fix is in" in msg["md"]


async def test_an_agent_on_beta_spawns_on_alpha(trusted):
    alpha, beta = trusted
    far = await beta.spawn(permission="write")
    out = await turn(far, mcp("session_spawn", agent="opus@alpha", prompt="hi"))
    assert out.startswith("mcp ok:"), out
    r = json.loads(out.removeprefix("mcp ok: "))
    (here,) = alpha.app.sessions.sessions.values()
    assert r["server"] == "alpha" and r["log_id"] == here.log_id
    m = here.meta()
    assert m["spawned_from"] == f"{far.handle}@beta"
    assert m["permission"] == "write"  # capped at the spawning agent's


async def test_an_agent_on_beta_lists_alphas_sessions(trusted):
    alpha, beta = trusted
    here, far = await alpha.spawn(), await beta.spawn()
    listed = json.loads((await turn(far, mcp("session_list"))).removeprefix("mcp ok: "))
    (entry,) = [e for e in listed if e.get("server") == "alpha"]
    assert entry["handle"] == here.handle and entry["state"] == here.status


async def test_a_person_on_beta_calls_alpha_but_cannot_watch_it(trusted):
    alpha, beta = trusted
    async with Browser(beta) as b:
        r = await b.call("agents.list", server="alpha")
        assert "result" in r and r["result"]["agents"]
        await b.send(t="sub", channel="sessions", server="alpha")
        await until(
            lambda: any(
                f.get("t") == "error" and f.get("server") == "alpha" for f in b.frames
            ),
            what="the refusal",
        )
        (err,) = [f for f in b.frames if f.get("t") == "error"]
        assert err["error"]["code"] == "not_both_ways"


async def test_a_call_back_is_not_relayed_to_a_third_server(
    trusted, tmp_path, fake_claude
):
    alpha, beta = trusted
    gamma = await Node(tmp_path / "gamma", "gamma", fake_claude).start()
    try:
        alpha.app.links.add("gamma", gamma.base, gamma.token)
        await until(lambda: alpha.app.links.get("gamma").state == "linked")
        far, there = await beta.spawn(), await gamma.spawn()
        r = await beta.app.links.get("alpha").call_raw(
            "peer.handoff", {"target": f"{there.handle}@gamma", "context": "x"}
        )
        assert r["error"]["code"] == "not_relayed", r
        assert not inbox(there) and far
    finally:
        await gamma.stop()


async def test_the_way_back_goes_when_the_link_does(trusted):
    alpha, beta = trusted
    alpha.app.links.remove("beta")
    await until(lambda: beta.app.links.get("alpha") is None, what="the way back gone")
    far = await beta.spawn()
    out = await turn(far, mcp("peer_handoff", target="anyone@alpha", context="x"))
    assert "unknown_server" in out


def test_aegis_link_add_trusted_names_the_refusal(tmp_path, monkeypatch):
    async def refuse(*a, **k):
        raise links.LinkError("trust_refused", "beta has tokens besides its admin's")

    monkeypatch.setattr(links, "probe", refuse)
    r = CliRunner().invoke(
        cli,
        [
            "link",
            "add",
            "beta",
            "http://127.0.0.1:9",
            "--trusted",
            "--root",
            str(tmp_path),
        ],
        input="tok\n",
    )
    assert r.exit_code == 1
    assert "not linked: beta has tokens besides its admin's" in r.output


# -- reading across ---------------------------------------------------------------------
async def test_session_list_shows_a_trusted_servers_sessions_in_full(trusted):
    alpha, beta = trusted
    here, far = await alpha.spawn(), await beta.spawn()
    beta.app.sessions.rename(far.log_id, None, "a far title")
    listed = json.loads(
        (await turn(here, mcp("session_list"))).removeprefix("mcp ok: ")
    )
    (entry,) = [e for e in listed if e.get("server") == "beta"]
    assert entry["handle"] == far.handle and entry["title"] == "a far title"
    assert "attention" in entry and "cwd" in entry and "you" not in entry


async def test_an_agent_reads_a_session_across_a_trusted_link_both_ways(trusted):
    alpha, beta = trusted
    here, far = await alpha.spawn(), await beta.spawn()
    await turn(far, "say the far marker")
    await turn(here, "say the near marker")
    out = await turn(here, mcp("peer_read", target=f"{far.handle}@beta"))
    assert out.startswith("mcp ok:") and "the far marker" in out, out
    out = await turn(far, mcp("peer_read", target=f"{here.handle}@alpha"))
    assert out.startswith("mcp ok:") and "the near marker" in out, out


async def test_a_trusted_servers_error_text_reaches_the_agent(trusted):
    alpha, beta = trusted
    here = await alpha.spawn()
    out = await turn(here, mcp("peer_handoff", target="nosuch-one@beta", context="x"))
    assert out.startswith("mcp error:") and "beta: no open session 'nosuch-one'" in out
    out = await turn(here, mcp("peer_read", target="nosuch-one@beta"))
    assert "beta: no open session 'nosuch-one'" in out, out


async def test_reading_across_a_plain_link_is_still_refused(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    try:
        alpha.app.links.add("beta", beta.base, beta.token)
        await until(lambda: alpha.app.links.get("beta").state == "linked")
        here, far = await alpha.spawn(), await beta.spawn()
        out = await turn(here, mcp("peer_read", target=f"{far.handle}@beta"))
        assert "not_across_links" in out and "trusted" in out, out
    finally:
        await alpha.stop()
        await beta.stop()


# -- acting across ----------------------------------------------------------------------
async def test_an_agent_enqueues_across_and_gets_the_result_back(trusted):
    alpha, beta = trusted
    here = await alpha.spawn()
    said = await turn(
        here,
        mcp("queue_enqueue", queue="general@beta", payload="summarize the README"),
    )
    assert said.startswith("mcp ok:"), said
    task_id = json.loads(said.removeprefix("mcp ok: "))["task_id"]
    far_id, _, server = task_id.partition("@")
    assert server == "beta" and far_id in beta.app.queues.tasks
    assert beta.app.queues.tasks[far_id].far_enqueuer == {
        "handle": here.handle,
        "server": "alpha",
    }
    await until(lambda: inbox(here), timeout=15, what="the result back on alpha")
    (cb,) = inbox(here)
    assert cb["title"].startswith(f"queue:general@beta · task#{task_id} · ok")
    assert "summarize the README" in cb["md"]
    said = await turn(here, mcp("task_status", task_id=task_id))
    assert said.startswith("mcp ok:") and '"completed"' in said, said


async def test_only_a_trusted_link_delivers_a_tasks_result(trusted):
    alpha, beta = trusted
    here = await alpha.spawn()
    params = {
        "target": here.handle,
        "queue": "general",
        "task_id": "task-ab12",
        "outcome": "ok",
        "body": "forged",
    }
    async with Browser(alpha) as b:
        r = await b.call("queue.deliver", **params)
        assert r["error"]["code"] == "not_a_link"
    r = await beta.app.links.get("alpha").call_raw(
        "queue.deliver", {**params, "queue": "general\n> from user"}
    )
    assert r["error"]["code"] == "bad_sender"
    assert not inbox(here)


async def test_an_agent_waits_on_a_session_across_a_trusted_link(trusted):
    alpha, beta = trusted
    here, far = await alpha.spawn(), await beta.spawn()
    said = await turn(
        here,
        mcp(
            "monitor_sessions",
            description="beta's work",
            sessions=[f"{far.handle}@beta"],
            interval_s=1,
        ),
    )
    assert said.startswith("mcp ok:"), said
    await turn(far, mcp("turn_end", attention="review", line="Read the diff."))
    await until(lambda: inbox(here), timeout=10, what="the wake")
    (wake,) = inbox(here)
    assert " · ok · " in wake["title"] and f"{far.handle}@beta review" in wake["md"]


async def test_waiting_on_an_unknown_far_session_is_refused(trusted):
    alpha, beta = trusted
    here = await alpha.spawn()
    said = await turn(
        here, mcp("monitor_sessions", description="x", sessions=["nosuch-one@beta"])
    )
    assert said.startswith("mcp error: no_session"), said


async def test_an_agent_closes_a_far_session_under_the_same_rule(trusted):
    alpha, beta = trusted
    here = await alpha.spawn()
    other = await beta.spawn()
    # One it did not start: refused with a token, closed on the second call.
    said = await turn(here, mcp("session_close", handle=f"{other.handle}@beta"))
    assert said.startswith("mcp error: second_thought"), said
    assert f'handle "{other.handle}@beta"' in said, said
    token = re.search(r'token "([^"]+)"', said).group(1)
    said = await turn(
        here, mcp("session_close", handle=f"{other.handle}@beta", token=token)
    )
    assert said.startswith("mcp ok:") and "closed" in said, said
    assert other.log_id not in beta.app.sessions.sessions
    # One it started, done: closed at once.
    said = await turn(here, mcp("session_spawn", agent="opus@beta", prompt="hi"))
    child = json.loads(said.removeprefix("mcp ok: "))["handle"].partition("@")[0]
    far = beta.app.sessions.by_handle(child)
    await turn(far, mcp("turn_end", attention="done", line="Done."))
    said = await turn(here, mcp("session_close", handle=f"{child}@beta"))
    assert said.startswith("mcp ok:") and "closed" in said, said


async def test_acting_across_a_plain_link_is_still_refused(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    try:
        alpha.app.links.add("beta", beta.base, beta.token)
        await until(lambda: alpha.app.links.get("beta").state == "linked")
        here, far = await alpha.spawn(), await beta.spawn()
        for call in (
            mcp("queue_enqueue", queue="general@beta", payload="x"),
            mcp("session_close", handle=f"{far.handle}@beta"),
            mcp("monitor_sessions", description="x", sessions=[f"{far.handle}@beta"]),
            mcp("task_status", task_id="task-ab12@beta"),
        ):
            said = await turn(here, call)
            assert "not_across_links" in said, (call, said)
        assert far.log_id in beta.app.sessions.sessions and not beta.app.queues.tasks
    finally:
        await alpha.stop()
        await beta.stop()
