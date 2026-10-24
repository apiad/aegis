# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""Proposals and Undo (knowledge_ops.py): an agent's own memory or skill is
proposed and shows a card, the person approves or discards it, and an
agent's change to an approved item applies at once with an Undo card."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from aegis.ops import OpError

from .conftest import until
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import inbox, mcp, turn, world  # noqa: F401  (fixture)
from .test_member_view import homes, spawn  # noqa: F401
from .test_member_view import world as members  # noqa: F401


def ok(said: str) -> dict:
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


def card(s, id: str) -> dict:
    return next(e for e in s.entries() if e["id"] == id)


def cards(s) -> list[dict]:
    return [e for e in s.entries() if e["kind"] == "knowledge"]


def woken(s, needle: str) -> bool:
    return needle in json.dumps(inbox(s), ensure_ascii=False)


async def press(world, s, id: str, op: str):
    return await world.app.registry.call(
        f"knowledge.card.{op}", {"log_id": s.log_id, "id": id}
    )


def hinted(app, s, text: str) -> str:
    return app.knowledge.hint_for(app.user, s.log_id, text, None)


async def save(s, name="merge-after-green", asked=False, **kw) -> dict:
    return ok(
        await turn(
            s,
            mcp(
                "memory_save",
                name=name,
                description=kw.pop("description", "Merge a PR only after every check"),
                body=kw.pop("body", "The author merges without being asked."),
                type="feedback",
                asked=asked,
                **kw,
            ),
            by_person=asked,
        )
    )


async def test_an_agents_own_memory_is_proposed_and_not_hinted(world):
    app = world.app
    s = await world.spawn()
    got = await save(s)
    assert got["status"] == "proposed" and got["card"]
    assert app.knowledge.store.get(app.user, "merge-after-green").status == "proposed"
    e = card(s, got["card"])
    assert e["kind"] == "knowledge" and e["status"] == "pending"
    d = e["detail"]
    assert d["op"] == "new" and d["item_kind"] == "memory"
    assert d["name"] == "merge-after-green" and d["type"] == "feedback"
    assert d["lifetime"] == "permanent" and "without being asked" in d["body"]
    assert "merge-after-green" not in hinted(app, s, "merge the PR after the checks")
    # Searchable and readable, marked.
    hits = ok(await turn(s, mcp("memory_search", query="merge check")))["hits"]
    assert hits[0]["name"] == "merge-after-green" and hits[0]["status"] == "proposed"


async def test_asked_in_a_persons_turn_saves_approved_with_an_undo_card(world):
    app = world.app
    s = await world.spawn()
    got = await save(s, asked=True)
    assert got["status"] == "approved" and set(got) == {"saved", "status", "card"}
    e = card(s, got["card"])
    assert e["detail"]["op"] == "saved" and len(e["detail"]["sha"]) == 40
    assert "merge-after-green" in hinted(app, s, "merge the PR after the checks")
    assert await press(world, s, got["card"], "undo") == "ok"
    assert app.knowledge.store.get(app.user, "merge-after-green") is None


async def test_asked_never_skips_the_undo_card_of_a_change(world):
    app = world.app
    s = await world.spawn()
    await save(s, asked=True)
    got = await save(s, asked=True, body="Only the required checks.")
    assert got["status"] == "approved" and card(s, got["card"])["detail"]["op"] == (
        "changed"
    )
    assert await press(world, s, got["card"], "undo") == "ok"
    it = app.knowledge.store.get(app.user, "merge-after-green")
    assert "without being asked" in it.body


async def test_asked_outside_a_persons_turn_is_a_proposal(world):
    app = world.app
    s = await world.spawn()
    said = await turn(
        s,
        mcp(
            "memory_save",
            name="merge-after-green",
            description="Merge a PR only after every check",
            body="The author merges without being asked.",
            asked=True,
        ),
    )
    got = ok(said)
    assert got["status"] == "proposed" and "person's own message" in got["note"]
    assert card(s, got["card"])["detail"]["op"] == "new"
    assert app.knowledge.store.get(app.user, "merge-after-green").status == "proposed"


async def test_asked_in_a_queue_task_is_a_proposal(world):
    app = world.app
    s = await world.spawn()
    payload = mcp(
        "memory_save",
        name="from-a-worker",
        description="A worker says it was asked",
        body="It was not.",
        asked=True,
    )
    said = await turn(
        s, mcp("queue_enqueue", queue="general", payload=payload), by_person=True
    )
    t = app.queues.tasks[ok(said)["task_id"]]
    await until(
        lambda: t.worker and cards(world.session(t.worker)),
        timeout=10,
        what="the worker's card",
    )
    assert app.knowledge.store.get(app.user, "from-a-worker").status == "proposed"
    assert [e["detail"]["op"] for e in cards(world.session(t.worker))] == ["new"]


async def test_the_turn_knows_whether_a_person_began_it(world):
    app = world.app
    s = await world.spawn()
    send = app.registry.call
    await send("session.send", {"log_id": s.log_id, "text": "hello"})
    assert s.person_turn
    await until(lambda: s.status == "idle", timeout=8, what="idle")
    await s.deliver(f"> from agent:{s.handle} · now", "a handoff")
    assert not s.person_turn
    await until(lambda: s.status == "idle", timeout=8, what="idle")
    await send("session.send", {"log_id": s.log_id, "text": "/remember the sky"})
    assert s.person_turn


async def test_approve_makes_it_hintable(world):
    app = world.app
    s = await world.spawn()
    id = (await save(s))["card"]
    assert await press(world, s, id, "approve") == "ok"
    assert card(s, id)["status"] == "approved"
    assert app.knowledge.store.get(app.user, "merge-after-green").status == "approved"
    assert "merge-after-green" in hinted(app, s, "merge the PR after the checks")
    await until(lambda: woken(s, f"knowledge:{id} · approved"), timeout=8, what="wake")
    with pytest.raises(OpError) as err:
        await press(world, s, id, "approve")
    assert err.value.code == "gone"


async def test_discard_deletes_the_proposal(world):
    app = world.app
    s = await world.spawn()
    id = (await save(s))["card"]
    assert await press(world, s, id, "discard") == "ok"
    assert card(s, id)["status"] == "discarded"
    assert app.knowledge.store.get(app.user, "merge-after-green") is None
    await until(lambda: woken(s, f"knowledge:{id} · discarded"), timeout=8, what="wake")


async def test_approve_refuses_a_text_the_card_did_not_show(world):
    app = world.app
    s = await world.spawn()
    first = (await save(s))["card"]
    second = (await save(s, body="Changed after the first card."))["card"]
    with pytest.raises(OpError) as err:
        await press(world, s, first, "approve")
    assert err.value.code == "stale" and card(s, first)["status"] == "stale"
    assert app.knowledge.store.get(app.user, "merge-after-green").status == "proposed"
    assert await press(world, s, second, "approve") == "ok"


async def test_an_agents_change_to_an_approved_memory_applies_with_undo(world):
    app = world.app
    s = await world.spawn()
    await save(s, asked=True)
    got = await save(s, body="Only the required checks.")
    assert got["status"] == "approved"
    e = card(s, got["card"])
    assert e["detail"]["op"] == "changed" and len(e["detail"]["sha"]) == 40
    assert "required" in app.knowledge.store.get(app.user, "merge-after-green").body
    assert await press(world, s, got["card"], "undo") == "ok"
    assert card(s, got["card"])["status"] == "undone"
    it = app.knowledge.store.get(app.user, "merge-after-green")
    assert "without being asked" in it.body and it.status == "approved"


async def test_undo_restores_a_deleted_memory(world):
    app = world.app
    s = await world.spawn()
    await save(s, asked=True)
    gone = ok(await turn(s, mcp("memory_delete", name="merge-after-green")))
    e = card(s, gone["card"])
    assert e["detail"]["op"] == "deleted" and e["detail"]["sha"] == gone["sha"]
    assert app.knowledge.store.get(app.user, "merge-after-green") is None
    assert await press(world, s, gone["card"], "undo") == "ok"
    it = app.knowledge.store.get(app.user, "merge-after-green")
    assert it is not None and "without being asked" in it.body
    assert "merge-after-green" in hinted(app, s, "merge the PR after the checks")
    await until(lambda: woken(s, "· undone"), timeout=8, what="wake")


async def test_deleting_its_own_proposal_leaves_no_undo_card(world):
    s = await world.spawn()
    id = (await save(s))["card"]
    gone = ok(await turn(s, mcp("memory_delete", name="merge-after-green")))
    assert "card" not in gone
    with pytest.raises(OpError) as err:
        await press(world, s, id, "approve")
    assert err.value.code == "gone" and card(s, id)["status"] == "gone"


async def test_cards_survive_a_restart(world):
    app = world.app
    s = await world.spawn()
    new = (await save(s))["card"]
    await save(s, name="other-fact", asked=True)
    gone = ok(await turn(s, mcp("memory_delete", name="other-fact")))["card"]
    lid = s.log_id
    await world.restart()
    s = world.session(lid)
    assert {new, gone} <= {e["id"] for e in cards(s)}
    assert await press(world, s, new, "approve") == "ok"
    assert await press(world, s, gone, "undo") == "ok"
    assert world.app.knowledge.store.get(app.user, "other-fact") is not None
    assert world.app.knowledge.store.get(app.user, "merge-after-green").status == (
        "approved"
    )


async def test_a_proposed_skill_is_not_a_command_but_skill_read_loads_it(world):
    app = world.app
    s = await world.spawn()
    got = ok(
        await turn(
            s,
            mcp(
                "skill_save", name="release", description="Cut a release", body="1. go"
            ),
        )
    )
    assert got["status"] == "proposed"
    assert "release" not in [n for n, _ in await app._offered_skills(app.user)]
    assert "release" not in hinted(app, s, "cut a release")
    read = ok(await turn(s, mcp("skill_read", name="release")))
    assert "1. go" in read["text"] and s.skills == ["release"]
    assert await press(world, s, got["card"], "approve") == "ok"
    assert "release" in [n for n, _ in await app._offered_skills(app.user)]


async def test_only_the_session_owner_answers_its_card(members):
    app = members.app
    ben = app.sessions.sessions[await spawn(members, BEN)]
    id = (await save(ben, name="ben-fact"))["card"]
    ref = {"log_id": ben.log_id, "id": id}
    # The admin may read ben's transcript, but the card is ben's to answer.
    with pytest.raises(OpError) as err:
        await app.registry.call("knowledge.card.approve", ref)
    assert err.value.code == "not_owner" and card(ben, id)["status"] == "pending"
    assert await app.registry.call("knowledge.card.approve", ref, BEN) == "ok"
    assert app.knowledge.store.get("ben", "ben-fact").status == "approved"
    assert app.knowledge.store.get(app.user, "ben-fact") is None


def records(s, kind: str) -> list[dict]:
    rows = [json.loads(ln) for ln in s.store.path.read_text().splitlines() if ln]
    return [r for r in rows if r.get("kind") == kind]


async def outcomes(*presses) -> list:
    got = await asyncio.gather(*presses, return_exceptions=True)
    return [g if isinstance(g, str) else g.code for g in got]


async def test_two_presses_at_once_act_once(world):
    app = world.app
    s = await world.spawn()
    id = (await save(s))["card"]
    got = await outcomes(press(world, s, id, "approve"), press(world, s, id, "approve"))
    assert sorted(got) == ["gone", "ok"]
    assert len(records(s, "knowledge_card_done")) == 1
    await until(lambda: woken(s, f"knowledge:{id} · approved"), timeout=8, what="wake")
    await until(lambda: s.status == "idle", timeout=8, what="idle")
    other = (await save(s, name="second-fact"))["card"]
    got = await outcomes(
        press(world, s, other, "approve"), press(world, s, other, "discard")
    )
    assert sorted(got) == ["gone", "ok"]
    assert card(s, other)["status"] in ("approved", "discarded")
    assert app.knowledge.store.get(app.user, "merge-after-green").status == "approved"


async def test_a_resave_racing_an_approve_never_approves_unseen_text(world):
    app = world.app
    s = await world.spawn()
    id = (await save(s))["card"]
    store = app.knowledge.store
    path = store.dir(app.user) / "memories" / "merge-after-green.md"
    async with store._lock(app.user):
        pressing = asyncio.create_task(press(world, s, id, "approve"))
        await asyncio.sleep(0.2)  # the press waits on the store's lock
        path.write_text(path.read_text().replace("without being asked", "never"))
    got = await outcomes(pressing)
    assert got == ["stale"] and card(s, id)["status"] == "stale"
    it = store.get(app.user, "merge-after-green")
    assert it.status == "proposed" and "never" in it.body


async def save_skill(s, body="1. go", files=None) -> dict:
    files = {k: base64.b64encode(v).decode() for k, v in (files or {}).items()}
    return ok(
        await turn(
            s,
            mcp(
                "skill_save",
                name="release",
                description="Cut a release",
                body=body,
                files=files,
            ),
        )
    )


async def test_a_skills_card_covers_every_file(world):
    s = await world.spawn()
    first = await save_skill(s, files={"bump.py": b"print(1)\n"})
    d = card(s, first["card"])["detail"]
    assert {"name": "bump.py", "size": 9} in d["files"]
    assert any(f["name"] == "SKILL.md" for f in d["files"]) and not d["partial"]
    # The same SKILL.md with a changed script: the first card is stale.
    second = await save_skill(s, files={"bump.py": b"import os; os.remove('/')\n"})
    with pytest.raises(OpError) as err:
        await press(world, s, first["card"], "approve")
    assert err.value.code == "stale"
    assert await press(world, s, second["card"], "approve") == "ok"


async def test_a_long_body_is_marked_shown_in_part(world):
    s = await world.spawn()
    got = await save_skill(s, body="step\n" * 1000)
    d = card(s, got["card"])["detail"]
    assert d["partial"] and len(d["body"]) == 4000


async def settled(s, id: str) -> None:
    """The card's wake has run its turn, so the next prompt is answered alone."""
    await until(lambda: woken(s, f"knowledge:{id} ·"), timeout=8, what="wake")
    await until(lambda: s.status == "idle", timeout=8, what="idle")


async def test_answering_a_card_refreshes_the_items_flags(world):
    app = world.app
    kn = app.knowledge
    s = await world.spawn()
    gone = "Read `gone/notes.md` first."
    # Undo of a change that quoted a missing path: the flag goes with it.
    await save(s, asked=True)
    changed = (await save(s, body=gone))["card"]
    await until(lambda: kn.flags_of(app.user, "merge-after-green") != [])
    assert await press(world, s, changed, "undo") == "ok"
    assert kn.flags_of(app.user, "merge-after-green") == []
    await settled(s, changed)
    # Discard of a flagged proposal: no flags are left for a deleted item.
    proposed = (await save(s, name="other-fact", body=gone))["card"]
    await until(lambda: kn.flags_of(app.user, "other-fact") != [])
    assert await press(world, s, proposed, "discard") == "ok"
    assert kn.flags_of(app.user, "other-fact") == []
    await settled(s, proposed)
    # Undo of an Undo-able delete brings the flags back with the memory.
    await save(s, name="third-fact", body=gone, asked=True)
    await until(lambda: kn.flags_of(app.user, "third-fact") != [])
    deleted = ok(await turn(s, mcp("memory_delete", name="third-fact")))["card"]
    assert kn.flags_of(app.user, "third-fact") == []
    assert await press(world, s, deleted, "undo") == "ok"
    assert kn.flags_of(app.user, "third-fact") == ["stale:gone/notes.md"]
