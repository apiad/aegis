# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""knowledge.import (knowledge_ops.py, knowledge/importer.py): what /setup
moves into the store, read as the session's user, approved, one commit each,
with one card that undoes each item on its own."""

from __future__ import annotations

import json
import threading

from aegis import setup
from aegis.knowledge import history, importer

from .conftest import until
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


async def bring(s, *items) -> dict:
    return ok(await turn(s, mcp("knowledge_import", items=list(items))))


async def test_a_skill_folder_with_a_binary_imports_whole(world, tmp_path):
    app = world.app
    s = await world.spawn()
    src = skill_dir(tmp_path / "harness")
    got = await bring(s, {"source": str(src), "kind": "skill"})
    assert got["imported"] == [{"source": str(src), "kind": "skill", "name": "release"}]
    assert got["refused"] == []
    store = app.knowledge.store
    assert store.files(app.user, "release") == ["SKILL.md", "logo.png", "scripts/bump.py"]
    assert store.read_file(app.user, "release", "logo.png") == BINARY
    it = store.get(app.user, "release")
    assert it.status == "approved" and it.description == "Use when cutting a release"
    assert it.meta["allowed-tools"] == "Bash"  # other harnesses' keys kept
    root = app.roots.state_root / "knowledge" / app.user
    assert history.log(root, "skills/release")[0]["message"] == "import skill release"
    assert src.is_dir()  # the original stays until its own row removes it
    assert "release" in app.knowledge.hint_for(app.user, s.log_id, "cut a release", None)


async def test_an_auto_memory_file_imports_with_its_type(world, tmp_path):
    app = world.app
    s = await world.spawn()
    f = tmp_path / "feedback_merge.md"
    f.write_text(
        "---\nname: merge-after-green\ndescription: Merge only after every check\n"
        "metadata:\n  type: feedback\n---\nThe author merges.\n"
    )
    old = tmp_path / "deploy_gap.md"
    old.write_text("---\nname: x\ndescription: A deploy gap\ntype: project\n---\nIt is open.\n")
    got = await bring(
        s,
        {"source": str(f), "kind": "memory", "name": "merge-after-green"},
        {"source": str(old), "kind": "memory", "expires": "2026-12-01"},
    )
    assert [i["name"] for i in got["imported"]] == ["merge-after-green", "deploy_gap"]
    it = app.knowledge.store.get(app.user, "merge-after-green")
    assert it.meta["type"] == "feedback" and "metadata" not in it.meta
    assert it.status == "approved" and it.lifetime == "permanent"
    assert it.meta["imported_from"] == str(f) and it.meta["source"] == "import"
    gap = app.knowledge.store.get(app.user, "deploy_gap")
    assert gap.meta["type"] == "project" and gap.lifetime == "temporary"
    assert str(gap.expires) == "2026-12-01"


async def test_a_doc_becomes_a_skill_with_when_as_description(world, tmp_path):
    app = world.app
    s = await world.spawn()
    doc = tmp_path / "canary.md"
    doc.write_text("---\nwhen: a harness released a version\n---\n# The canary\n\nRun it.\n")
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
        s, {"source": str(src), "kind": "skill"}, {"source": str(inner), "kind": "skill"}
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
        "knowledge.card.undo_item", {"log_id": s.log_id, "id": got["card"], "name": "host"}
    )
    assert said == "ok"
    assert app.knowledge.store.get(app.user, "host") is None
    assert app.knowledge.store.get(app.user, "release") is not None
    e = next(x for x in s.entries() if x["id"] == got["card"])
    assert [x.get("outcome") for x in e["detail"]["items"]] == [None, "undone"]
    assert e["status"] == "pending"  # the other row can still be undone
    await until(lambda: "undid the import" in json.dumps(inbox(s)), timeout=8, what="wake")
