# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""knowledge.import (knowledge_ops.py, knowledge/importer.py): what /setup
moves into the store, read as the session's user, approved, one commit each,
with one card that undoes each item on its own."""

from __future__ import annotations

import json
import threading

from aegis import setup
from aegis.knowledge import history, importer

from aegis.ops import Caller, OpError

from .conftest import until
from .test_artifacts_e2e import landed
from .knowledge_samples import mem
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import inbox, mcp, turn, world  # noqa: F401  (fixture)
from .test_member_view import homes, spawn  # noqa: F401
from .test_member_view import world as members  # noqa: F401

BINARY = bytes(range(256)) * 4


def ok(said: str) -> dict:
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


def skill_dir(root, name="release", desc="Use when cutting a release"):
    d = root / name
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nallowed-tools: Bash\n---\n1. Bump.\n"
    )
    (d / "scripts" / "bump.py").write_text("print('bumped')\n")
    (d / "logo.png").write_bytes(BINARY)
    return d


async def bring(s, *items, by_person: bool = True) -> dict:
    """An import in a turn the person began, as the submit of /setup's page
    begins one, unless ``by_person`` is false."""
    said = await turn(
        s, mcp("knowledge_import", items=list(items)), by_person=by_person
    )
    return ok(said)


def cards(s) -> list[dict]:
    return [e for e in s.entries() if e["kind"] == "knowledge"]


async def test_a_skill_folder_with_a_binary_imports_whole(world, tmp_path):
    app = world.app
    s = await world.spawn()
    src = skill_dir(tmp_path / "harness")
    got = await bring(s, {"source": str(src), "kind": "skill"})
    assert got["imported"] == [{"source": str(src), "kind": "skill", "name": "release"}]
    assert got["refused"] == []
    store = app.knowledge.store
    assert store.files(app.user, "release") == [
        "SKILL.md",
        "logo.png",
        "scripts/bump.py",
    ]
    assert store.read_file(app.user, "release", "logo.png") == BINARY
    it = store.get(app.user, "release")
    assert it.status == "approved" and it.description == "Use when cutting a release"
    assert it.meta["allowed-tools"] == "Bash"  # other harnesses' keys kept
    root = app.roots.state_root / "knowledge" / app.user
    assert history.log(root, "skills/release")[0]["message"] == "import skill release"
    assert src.is_dir()  # the original stays until its own row removes it
    assert "release" in app.knowledge.hint_for(
        app.user, s.log_id, "cut a release", None
    )


async def test_an_auto_memory_file_imports_with_its_type(world, tmp_path):
    app = world.app
    s = await world.spawn()
    f = tmp_path / "feedback_merge.md"
    f.write_text(
        "---\nname: merge-after-green\ndescription: Merge only after every check\n"
        "metadata:\n  type: feedback\n---\nThe author merges.\n"
    )
    old = tmp_path / "deploy_gap.md"
    old.write_text(
        "---\nname: deploy-gap\ndescription: A deploy gap\ntype: project\n---\nIt is open.\n"
    )
    got = await bring(
        s,
        {"source": str(f), "kind": "memory", "name": "merge-after-green"},
        {"source": str(old), "kind": "memory", "expires": "2026-12-01"},
    )
    assert [i["name"] for i in got["imported"]] == ["merge-after-green", "deploy-gap"]
    it = app.knowledge.store.get(app.user, "merge-after-green")
    assert it.meta["type"] == "feedback" and "metadata" not in it.meta
    assert it.status == "approved" and it.lifetime == "permanent"
    assert it.meta["imported_from"] == str(f) and it.meta["source"] == "import"
    gap = app.knowledge.store.get(app.user, "deploy-gap")
    assert gap.meta["type"] == "project" and gap.lifetime == "temporary"
    assert str(gap.expires) == "2026-12-01"


async def test_a_doc_becomes_a_skill_with_when_as_description(world, tmp_path):
    app = world.app
    s = await world.spawn()
    doc = tmp_path / "canary.md"
    doc.write_text(
        "---\nwhen: a harness released a version\n---\n# The canary\n\nRun it.\n"
    )
    bare = tmp_path / "notes.md"
    bare.write_text("# Notes\n\nNo when line.\n")
    got = await bring(
        s, {"source": str(doc), "kind": "doc"}, {"source": str(bare), "kind": "doc"}
    )
    assert got["imported"] == [{"source": str(doc), "kind": "skill", "name": "canary"}]
    assert got["refused"][0]["error"] == "bad_source"
    assert "when:" in got["refused"][0]["message"]
    it = app.knowledge.store.get(app.user, "canary")
    assert it.kind == "skill" and it.description == "a harness released a version"
    assert "when" not in it.meta and it.body.startswith("# The canary")


async def test_a_name_collision_is_reported_not_overwritten(world, tmp_path):
    app = world.app
    s = await world.spawn()
    await app.knowledge.store.save(app.user, mem("release", "A memory"), "t")
    await app.knowledge.store.save(app.user, mem("deploy", "Mine"), "t")
    f = tmp_path / "deploy.md"
    f.write_text("---\ndescription: Theirs\n---\nOther text.\n")
    got = await bring(
        s,
        {"source": str(skill_dir(tmp_path)), "kind": "skill"},
        {"source": str(f), "kind": "memory"},
    )
    assert got["imported"] == []
    assert [r["error"] for r in got["refused"]] == ["name_taken", "name_taken"]
    assert app.knowledge.store.get(app.user, "release").kind == "memory"
    assert app.knowledge.store.get(app.user, "deploy").description == "Mine"
    assert "card" not in got


async def test_a_link_that_leaves_the_folder_is_refused(world, tmp_path):
    app = world.app
    s = await world.spawn()
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    src = skill_dir(tmp_path / "h")
    (src / "key").symlink_to(secret)
    inner = skill_dir(tmp_path / "h", "inner")
    (inner / "again.py").symlink_to(inner / "scripts" / "bump.py")
    got = await bring(
        s,
        {"source": str(src), "kind": "skill"},
        {"source": str(inner), "kind": "skill"},
    )
    assert [r["error"] for r in got["refused"]] == ["bad_source"]
    assert "leaves" in got["refused"][0]["message"]
    assert app.knowledge.store.get(app.user, "release") is None
    files = app.knowledge.store.files(app.user, "inner")
    assert "again.py" in files  # a link inside the folder is its file


async def test_a_folder_over_the_cap_is_refused(world, tmp_path, monkeypatch):
    s = await world.spawn()
    monkeypatch.setattr(importer, "MAX_FOLDER_BYTES", 512)
    got = await bring(s, {"source": str(skill_dir(tmp_path)), "kind": "skill"})
    assert [r["error"] for r in got["refused"]] == ["too_large"]


async def test_reads_never_run_on_the_event_loop(world, tmp_path, monkeypatch):
    s = await world.spawn()
    loop = threading.current_thread()
    threads = []
    real = importer.load

    def load(*a):
        threads.append(threading.current_thread())
        return real(*a)

    monkeypatch.setattr(importer, "load", load)
    await bring(s, {"source": str(skill_dir(tmp_path)), "kind": "skill"})
    assert threads and loop not in threads


async def test_a_member_imports_from_their_own_home_only(members, homes, monkeypatch):
    app = members.app
    ben = app.sessions.sessions[await spawn(members, BEN)]
    home = __import__("pathlib").Path(homes["ben"].pw_dir)
    src = skill_dir(home / ".claude" / "skills")
    threads = []
    real = importer.load

    def load(*a):
        threads.append(threading.current_thread().name)
        return real(*a)

    monkeypatch.setattr(importer, "load", load)
    got = await bring(ben, {"source": str(src), "kind": "skill"})
    assert [i["name"] for i in got["imported"]] == ["release"]
    assert threads and all(t.startswith("as-ben") for t in threads)
    assert app.knowledge.store.get("ben", "release") is not None
    assert app.knowledge.store.get(app.user, "release") is None
    assert app.setup_records.imported("ben")[0]["source"] == str(src)
    assert app.setup_records.imported(app.user) == []


async def test_the_record_keeps_the_import_and_the_next_setup_names_it(world, tmp_path):
    app = world.app
    s = await world.spawn()
    src = skill_dir(tmp_path)
    await bring(s, {"source": str(src), "kind": "skill"})
    said = await turn(
        s,
        mcp(
            "setup_record",
            applied=["queue:tests"],
            declined=[],
            sources_until="2026-10-24T00:00:00Z",
        ),
    )
    assert said.startswith("mcp ok"), said
    rec = app.setup_records.get(app.user)
    assert rec["applied"] == ["queue:tests"]
    assert [r["name"] for r in rec["imported"]] == ["release"]
    # A fresh Records reads the file, as after a restart.
    again = setup.Records(app.roots.state_root).get(app.user)
    assert again["imported"][0]["source"] == str(src)
    _, body = setup.turn("", "facts", again, "now")
    assert f"{src} as skill release" in body


async def test_the_card_undoes_one_item_and_keeps_the_rest(world, tmp_path):
    app = world.app
    s = await world.spawn()
    f = tmp_path / "host.md"
    f.write_text("---\ndescription: The bot runs on ben-2\n---\nben-2.\n")
    got = await bring(
        s,
        {"source": str(skill_dir(tmp_path)), "kind": "skill"},
        {"source": str(f), "kind": "memory"},
    )
    e = next(x for x in s.entries() if x["id"] == got["card"])
    assert e["kind"] == "knowledge" and e["detail"]["op"] == "imported"
    assert [x["name"] for x in e["detail"]["items"]] == ["release", "host"]
    said = await app.registry.call(
        "knowledge.card.unimport",
        {"log_id": s.log_id, "id": got["card"], "name": "host"},
    )
    assert said == "ok"
    assert app.knowledge.store.get(app.user, "host") is None
    assert app.knowledge.store.get(app.user, "release") is not None
    e = next(x for x in s.entries() if x["id"] == got["card"])
    assert [x.get("outcome") for x in e["detail"]["items"]] == [None, "undone"]
    assert e["status"] == "pending"  # the other row can still be undone
    await until(
        lambda: "undid the import" in json.dumps(inbox(s)), timeout=8, what="wake"
    )


async def test_a_person_turn_imports_approved_with_one_card(world, tmp_path):
    app = world.app
    s, aid = await landed(world, tmp_path)
    await app.registry.call(
        "artifact.submit",
        {
            "log_id": s.log_id,
            "artifact_id": aid,
            "data": {"rows": ["x"]},
            "label": "Apply",
        },
    )
    await until(lambda: s.status == "idle", timeout=8, what="the submit's turn")
    assert s.person_turn  # the person's submit began it, as /setup's apply
    f = tmp_path / "host.md"
    f.write_text("---\ndescription: The bot runs on ben-2\n---\nben-2.\n")
    got = await app.registry.call(
        "knowledge.import",
        {"items": [{"source": str(f), "kind": "memory"}]},
        Caller("agent", log_id=s.log_id),
    )
    assert got["imported"][0]["name"] == "host" and got["status"] == "approved"
    assert app.knowledge.store.get(app.user, "host").status == "approved"
    assert [e["detail"]["op"] for e in cards(s)] == ["imported"]


async def test_a_worker_turn_imports_proposed_with_proposal_cards(world, tmp_path):
    app = world.app
    s = await world.spawn()
    f = tmp_path / "host.md"
    f.write_text("---\ndescription: The bot runs on ben-2\n---\nben-2.\n")
    payload = mcp("knowledge_import", items=[{"source": str(f), "kind": "memory"}])
    said = await turn(
        s, mcp("queue_enqueue", queue="general", payload=payload), by_person=True
    )
    t = app.queues.tasks[ok(said)["task_id"]]
    await until(
        lambda: t.worker and cards(world.session(t.worker)),
        timeout=10,
        what="the worker's card",
    )
    assert app.knowledge.store.get(app.user, "host").status == "proposed"
    (e,) = cards(world.session(t.worker))
    assert e["detail"]["op"] == "new" and e["detail"]["name"] == "host"
    assert app.setup_records.imported(app.user)[0]["name"] == "host"


async def test_a_handoff_turn_imports_proposed(world, tmp_path):
    app = world.app
    s = await world.spawn()
    await s.deliver(f"> from agent:{s.handle} · now", "move these in")
    await until(lambda: s.status == "idle", timeout=8, what="idle")
    assert not s.person_turn
    got = await app.registry.call(
        "knowledge.import",
        {"items": [{"source": str(skill_dir(tmp_path)), "kind": "skill"}]},
        Caller("agent", log_id=s.log_id),
    )
    assert got["imported"][0]["name"] == "release" and got["status"] == "proposed"
    assert app.knowledge.store.get(app.user, "release").status == "proposed"
    assert [e["detail"]["op"] for e in cards(s)] == ["new"]
    assert "release" not in app.knowledge.hint_for(
        app.user, s.log_id, "cut a release", None
    )


async def test_a_person_imports_into_a_session_they_name(world, tmp_path):
    app = world.app
    s = await world.spawn()
    f = tmp_path / "host.md"
    f.write_text("---\ndescription: The bot runs on ben-2\n---\nben-2.\n")
    items = [{"source": str(f), "kind": "memory"}]
    try:
        await app.registry.call("knowledge.import", {"items": items})
        raise AssertionError("a person's import needs a session for its card")
    except OpError as e:
        assert e.code == "bad_params" and "log_id" in e.message
    got = await app.registry.call(
        "knowledge.import", {"items": items, "log_id": s.log_id}
    )
    assert got["status"] == "approved"
    assert app.knowledge.store.get(app.user, "host").status == "approved"
    (e,) = cards(s)
    assert e["id"] == got["card"] and e["detail"]["op"] == "imported"


async def test_an_unreadable_subfolder_refuses_the_whole_skill(world, tmp_path):
    app = world.app
    s = await world.spawn()
    src = skill_dir(tmp_path)
    locked = src / "private"
    locked.mkdir()
    (locked / "notes.md").write_text("hidden")
    locked.chmod(0)
    try:
        got = await bring(s, {"source": str(src), "kind": "skill"})
    finally:
        locked.chmod(0o755)
    assert got["imported"] == []
    (r,) = got["refused"]
    assert r["error"] == "bad_source" and "private" in r["message"]
    assert "cannot be read" in r["message"]
    assert app.knowledge.store.get(app.user, "release") is None


async def test_a_folder_with_too_many_files_is_refused(world, tmp_path, monkeypatch):
    s = await world.spawn()
    assert importer.MAX_FOLDER_FILES == 2000
    monkeypatch.setattr(importer, "MAX_FOLDER_FILES", 5)
    src = skill_dir(tmp_path)
    for i in range(5):
        (src / f"f{i}.txt").write_text("x")
    got = await bring(s, {"source": str(src), "kind": "skill"})
    (r,) = got["refused"]
    assert r["error"] == "too_large" and "5 files" in r["message"]


async def test_a_fifo_in_the_folder_refuses_the_skill(world, tmp_path):
    import os

    s = await world.spawn()
    src = skill_dir(tmp_path)
    os.mkfifo(src / "pipe")
    got = await bring(s, {"source": str(src), "kind": "skill"})
    (r,) = got["refused"]
    assert r["error"] == "bad_source" and "not a regular file" in r["message"]


async def test_a_memory_or_doc_over_a_mebibyte_is_refused_unread(world, tmp_path):
    s = await world.spawn()
    assert importer.MAX_FILE_BYTES == 2**20
    big = tmp_path / "big.md"
    big.write_text("---\ndescription: Big\nwhen: always\n---\n" + "x" * 2**20)
    got = await bring(
        s, {"source": str(big), "kind": "memory"}, {"source": str(big), "kind": "doc"}
    )
    assert [r["error"] for r in got["refused"]] == ["too_large", "too_large"]
    assert "1048576" in got["refused"][0]["message"]


async def test_a_memory_takes_its_frontmatter_name_so_links_still_resolve(
    world, tmp_path
):
    """Claude Code names a memory in its frontmatter and links by that name;
    the file's stem is only where it was kept."""
    app = world.app
    s = await world.spawn()
    a = tmp_path / "feedback_merge_after_green.md"
    a.write_text(
        "---\nname: merge-after-green\ndescription: Merge only after every check\n"
        "metadata:\n  type: feedback\n---\nSee [[deploy-hosts]].\n"
    )
    b = tmp_path / "reference_hosts.md"
    b.write_text(
        "---\nname: deploy-hosts\ndescription: Where each bot runs\n---\n"
        "Linked from [[merge-after-green]].\n"
    )
    odd = tmp_path / "project_gap.md"
    odd.write_text("---\nname: Not A Name\ndescription: A gap\n---\nOpen.\n")
    got = await bring(s, *({"source": str(f), "kind": "memory"} for f in (a, b, odd)))
    assert [i["name"] for i in got["imported"]] == [
        "merge-after-green",
        "deploy-hosts",
        "project_gap",  # an unusable name: the stem
    ]
    store = app.knowledge.store
    for it in (store.get(app.user, n) for n in ("merge-after-green", "deploy-hosts")):
        (link,) = __import__("re").findall(r"\[\[([^\]]+)\]\]", it.body)
        assert store.get(app.user, link) is not None, link


def memory_file(root, name: str):
    f = root / f"{name}.md"
    f.write_text(f"---\ndescription: The fact {name}\n---\n{name}.\n")
    return {"source": str(f), "kind": "memory"}


async def test_a_failure_after_the_first_item_keeps_its_card(
    world, tmp_path, monkeypatch
):
    app = world.app
    s = await world.spawn()
    real = importer.load
    calls = []

    def load(*a):
        calls.append(a)
        if len(calls) > 1:
            raise RuntimeError("the disk went away")
        return real(*a)

    monkeypatch.setattr(importer, "load", load)
    got = await bring(s, memory_file(tmp_path, "one"), memory_file(tmp_path, "two"))
    assert [i["name"] for i in got["imported"]] == ["one"]
    (r,) = got["refused"]
    assert r["error"] == "failed" and "the disk went away" in r["message"]
    assert app.knowledge.store.get(app.user, "two") is None
    (e,) = cards(s)
    assert e["id"] == got["card"] and [x["name"] for x in e["detail"]["items"]] == [
        "one"
    ]


async def test_a_failing_record_write_still_cards_what_came_in(
    world, tmp_path, monkeypatch
):
    app = world.app
    s = await world.spawn()

    def broken(*a):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(app.setup_records, "add_imported", broken)
    got = await bring(s, memory_file(tmp_path, "one"))
    assert [i["name"] for i in got["imported"]] == ["one"]
    assert "No space left" in got["error"]
    assert app.knowledge.store.get(app.user, "one") is not None
    (e,) = cards(s)
    assert e["id"] == got["card"]


async def test_one_call_imports_at_most_fifty_items(world, tmp_path):
    s = await world.spawn()
    items = [memory_file(tmp_path, f"m{i}") for i in range(51)]
    said = await turn(s, mcp("knowledge_import", items=items), by_person=True)
    assert said.startswith("mcp error: too_many"), said
    assert "split" in said


async def test_an_undone_import_is_proposed_again_by_the_next_setup(world, tmp_path):
    app = world.app
    s = await world.spawn()
    src = skill_dir(tmp_path)
    host = memory_file(tmp_path, "host")
    got = await bring(s, {"source": str(src), "kind": "skill"}, host)
    said = await app.registry.call(
        "knowledge.card.unimport",
        {"log_id": s.log_id, "id": got["card"], "name": "release"},
    )
    assert said == "ok"
    assert [r["name"] for r in app.setup_records.imported(app.user)] == ["host"]
    _, body = setup.turn("", "facts", app.setup_records.get(app.user), "now")
    assert str(src) not in body and f"{host['source']} as memory host" in body


async def test_a_discarded_import_proposal_leaves_the_record(world, tmp_path):
    app = world.app
    s = await world.spawn()
    got = await bring(s, memory_file(tmp_path, "host"), by_person=False)
    (id,) = got["cards"]
    said = await app.registry.call(
        "knowledge.card.discard", {"log_id": s.log_id, "id": id}
    )
    assert said == "ok"
    assert app.setup_records.imported(app.user) == []


async def test_undo_refuses_a_skill_that_gained_files_since_the_import(world, tmp_path):
    app = world.app
    s = await world.spawn()
    got = await bring(s, {"source": str(skill_dir(tmp_path)), "kind": "skill"})
    store = app.knowledge.store
    it = store.get(app.user, "release")
    held = {
        n: store.read_file(app.user, "release", n)
        for n in store.files(app.user, "release")
        if n != "SKILL.md"
    }
    await store.save(app.user, it, "t", extra={**held, "notes.md": b"mine"})
    before = store.files(app.user, "release")
    try:
        await app.registry.call(
            "knowledge.card.unimport",
            {"log_id": s.log_id, "id": got["card"], "name": "release"},
        )
        raise AssertionError("the undo should be refused")
    except OpError as e:
        assert e.code == "conflict" and "notes.md" in e.message
    assert store.files(app.user, "release") == before
    assert store.get(app.user, "release") is not None
    assert app.setup_records.imported(app.user)[0]["name"] == "release"
