"""The Files view's operations through Registry.call, on a real App."""

import asyncio
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from aegis import fs_trash
from aegis.app import App
from aegis.ops import Caller, OpError, Principal
from aegis.roots import make_roots

CONFIG = "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
MEMBER = Caller("user", principal=Principal("bea", "member"))


@pytest.fixture
def app(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\r\ny = 2\r\n")
    app = App(
        make_roots(tmp_path, tmp_path),
        claude_bin=fake_claude,
        opencode_bin=fake_opencode,
    )
    app.roots.state_root.mkdir(parents=True, exist_ok=True)
    (app.roots.state_root / "token").write_text("secret")
    return app


async def call(app, name, params, caller=MEMBER):
    return await app.registry.call(name, params, caller)


async def refused(app, name, params, caller=MEMBER) -> OpError:
    with pytest.raises(OpError) as e:
        await call(app, name, params, caller)
    return e.value


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def test_read_answers_text_kind_and_hash(app):
    r = await call(app, "fs.read", {"path": "src/a.py"})
    assert r["text"] == "x = 1\r\ny = 2\r\n"
    assert r["kind"] == "text" and r["hash"] == sha(r["text"])
    assert r["size"] == 14 and "cut" not in r


async def test_read_refuses_the_state_root(app):
    e = await refused(app, "fs.read", {"path": ".aegis/state/token"})
    assert e.code == "forbidden"


async def test_a_binary_file_has_no_text(app, tmp_path):
    (tmp_path / "blob.dat").write_bytes(b"\x00\x01\xff" * 10)
    r = await call(app, "fs.read", {"path": "blob.dat"})
    assert r["kind"] == "other" and "text" not in r


async def test_a_large_text_is_cut(app, tmp_path):
    (tmp_path / "big.txt").write_text("é" * (1024 * 1024 + 10))
    r = await call(app, "fs.read", {"path": "big.txt"})
    assert r["cut"] is True and len(r["text"].encode()) <= 2 * 1024 * 1024


async def test_write_with_the_right_base_saves_and_logs_the_member(app, tmp_path):
    path = tmp_path / "src" / "a.py"
    os.chmod(path, 0o750)
    base = (await call(app, "fs.read", {"path": "src/a.py"}))["hash"]
    r = await call(
        app, "fs.write", {"path": "src/a.py", "content": "x = 3\r\n", "base": base}
    )
    assert path.read_bytes() == b"x = 3\r\n"
    assert r["hash"] == sha("x = 3\r\n")
    assert path.stat().st_mode & 0o777 == 0o750
    log = (app.roots.state_root / "fs-log.jsonl").read_text().splitlines()
    line = json.loads(log[-1])
    assert line["who"] == "bea" and line["op"] == "write"
    assert line["path"] == "src/a.py" and line["size"] == 7


async def test_write_with_a_stale_base_changes_nothing(app, tmp_path):
    base = (await call(app, "fs.read", {"path": "src/a.py"}))["hash"]
    (tmp_path / "src" / "a.py").write_text("agent was here\n")
    e = await refused(
        app, "fs.write", {"path": "src/a.py", "content": "mine\n", "base": base}
    )
    assert e.code == "changed_on_disk"
    assert e.data == {"text": "agent was here\n", "hash": sha("agent was here\n")}
    assert (tmp_path / "src" / "a.py").read_text() == "agent was here\n"
    assert not (app.roots.state_root / "fs-log.jsonl").exists()


async def test_a_null_base_creates_and_refuses_an_existing_file(app, tmp_path):
    await call(app, "fs.write", {"path": "src/new.py", "content": "n\n", "base": None})
    assert (tmp_path / "src" / "new.py").read_text() == "n\n"
    e = await refused(
        app, "fs.write", {"path": "src/a.py", "content": "n\n", "base": None}
    )
    assert e.code == "exists"
    files = [p for p, _ in (await call(app, "fs.find", {}))["files"]]
    assert "src/new.py" in files


async def test_stat_answers_size_and_mtime(app, tmp_path):
    r = await call(app, "fs.stat", {"path": "src/a.py"})
    assert r["size"] == 14 and r["kind"] == "text"
    assert r["mtime"] == (tmp_path / "src" / "a.py").stat().st_mtime_ns // 1_000_000
    e = await refused(app, "fs.stat", {"path": "src/gone.py"})
    assert e.code == "not_found"


async def test_find_lists_files_newest_first(app, tmp_path):
    os.utime(tmp_path / "src" / "a.py", (1, 1))
    files = (await call(app, "fs.find", {}))["files"]
    assert files[-1][0] == "src/a.py"
    assert ".aegis.yaml" in [p for p, _ in files]


async def test_an_agent_is_refused(app):
    e = await refused(app, "fs.read", {"path": "src/a.py"}, Caller("agent", "log1"))
    assert e.code == "not_for_agents"


async def test_a_linked_server_is_refused(app):
    for name, params in [
        ("fs.find", {}),
        ("fs.read", {"path": "src/a.py"}),
        ("fs.stat", {"path": "src/a.py"}),
        ("fs.write", {"path": "src/x.py", "content": "", "base": None}),
        ("fs.list", {}),
        ("fs.mkdir", {"path": "x"}),
        ("fs.move", {"path": "src/a.py", "to": "b.py"}),
        ("fs.delete", {"path": "src/a.py"}),
        ("fs.restore", {"trash_id": "20261017-000000-00000000"}),
    ]:
        e = await refused(app, name, params, Caller("user", link="far"))
        assert e.code == "not_across_links"


async def test_two_saves_on_one_base_cannot_both_land(app, tmp_path):
    base = (await call(app, "fs.read", {"path": "src/a.py"}))["hash"]
    results = await asyncio.gather(
        *[
            call(app, "fs.write", {"path": "src/a.py", "content": t, "base": base})
            for t in ("tab one\n", "tab two\n")
        ],
        return_exceptions=True,
    )
    refused = [r for r in results if isinstance(r, OpError)]
    assert [e.code for e in refused] == ["changed_on_disk"]
    won = "tab one\n" if isinstance(results[1], OpError) else "tab two\n"
    assert (tmp_path / "src" / "a.py").read_text() == won
    log = (app.roots.state_root / "fs-log.jsonl").read_text().splitlines()
    assert len(log) == 1


async def test_a_save_over_a_cut_file_is_refused(app, tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * (3 * 1024 * 1024))
    r = await call(app, "fs.read", {"path": "big.txt"})
    assert r["cut"]
    e = await refused(
        app, "fs.write", {"path": "big.txt", "content": "tiny", "base": r["hash"]}
    )
    assert e.code == "too_large"
    assert big.stat().st_size == 3 * 1024 * 1024


async def test_stat_refuses_a_folder(app):
    e = await refused(app, "fs.stat", {"path": "src"})
    assert e.code == "not_a_file"


@pytest.fixture
def escapes(app, tmp_path):
    """Paths every operation must refuse, by what each is: '..', a symlink
    out of the root, one into the state root, and a folder symlinked out."""
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside\n")
    (tmp_path / "out").symlink_to(outside / "secret.txt")
    (tmp_path / "tok").symlink_to(app.roots.state_root / "token")
    (tmp_path / "outdir").symlink_to(outside)
    return outside


REFUSED = [
    ("../escape.txt", "outside_root"),
    ("out", "outside_root"),
    ("tok", "forbidden"),
    ("outdir/new.txt", "outside_root"),
]


@pytest.mark.parametrize("rel, code", REFUSED)
async def test_write_refuses_every_escape_and_writes_nothing(
    app, tmp_path, escapes, rel, code
):
    for base in (None, sha("x")):
        e = await refused(app, "fs.write", {"path": rel, "content": "x", "base": base})
        assert e.code == code
    assert (escapes / "secret.txt").read_text() == "outside\n"
    assert not (escapes / "new.txt").exists()
    assert not (tmp_path.parent / "escape.txt").exists()
    assert (app.roots.state_root / "token").read_text() == "secret"
    assert not (app.roots.state_root / "fs-log.jsonl").exists()


@pytest.mark.parametrize("rel, code", REFUSED[:3])
async def test_stat_and_read_refuse_every_escape(app, escapes, rel, code):
    for op in ("fs.stat", "fs.read"):
        e = await refused(app, op, {"path": rel})
        assert e.code == code


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


async def test_list_hides_ignored_entries_but_not_a_repository_under_them(
    app, tmp_path
):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("repos/\n*.log\n")
    (tmp_path / "run.log").write_text("noise\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "repos" / "one").mkdir(parents=True)
    git(tmp_path / "repos" / "one", "init", "-q")
    (tmp_path / "repos" / "scratch").mkdir()
    r = await call(app, "fs.list", {})
    names = [e["name"] for e in r["entries"]]
    # Folders first, then files, each alphabetical.
    assert [n for n in names if n in ("repos", "src", ".aegis.yaml", ".gitignore")] == [
        "repos",
        "src",
        ".aegis.yaml",
        ".gitignore",
    ]
    assert not {"run.log", "node_modules", ".aegis", ".git"} & set(names)
    assert next(e for e in r["entries"] if e["name"] == "repos")["ignored"]
    every = await call(app, "fs.list", {"dir": "", "ignored": True})
    shown = {e["name"]: e for e in every["entries"]}
    assert ".git" not in shown and shown["run.log"]["ignored"]
    assert shown["node_modules"]["ignored"] and shown[".aegis"]["ignored"]
    inside = (await call(app, "fs.list", {"dir": "repos"}))["entries"]
    assert [(e["name"], e["repo"], e["ignored"]) for e in inside] == [
        ("one", True, False)
    ]
    dot = await call(app, "fs.list", {"dir": ".aegis", "ignored": True})
    assert "state" not in [e["name"] for e in dot["entries"]]


async def test_mkdir_and_move_refuse_a_taken_name(app, tmp_path):
    await call(app, "fs.mkdir", {"path": "docs"})
    assert (tmp_path / "docs").is_dir()
    e = await refused(app, "fs.mkdir", {"path": "src"})
    assert e.code == "exists"
    (tmp_path / "src" / "b.py").write_text("b\n")
    e = await refused(app, "fs.move", {"path": "src/a.py", "to": "src/b.py"})
    assert e.code == "exists"
    assert (tmp_path / "src" / "b.py").read_text() == "b\n"
    await call(app, "fs.move", {"path": "src", "to": "docs/src"})
    assert (tmp_path / "docs" / "src" / "a.py").exists()
    files = [p for p, _ in (await call(app, "fs.find", {}))["files"]]
    assert "docs/src/a.py" in files and "src/a.py" not in files
    e = await refused(app, "fs.move", {"path": "docs", "to": "docs/inner"})
    assert e.code == "forbidden"
    log = [json.loads(x) for x in (app.roots.state_root / "fs-log.jsonl").open()]
    assert [(x["op"], x["path"], x["to"]) for x in log] == [
        ("mkdir", "docs", None),
        ("move", "src", "docs/src"),
    ]


async def test_delete_then_restore_gives_back_the_same_bytes(app, tmp_path):
    (tmp_path / "src" / "deep").mkdir()
    (tmp_path / "src" / "deep" / "d.bin").write_bytes(b"\x00\xff" * 50)
    before = (tmp_path / "src" / "a.py").read_bytes()
    r = await call(app, "fs.delete", {"path": "src"})
    assert r["files"] == 2 and not (tmp_path / "src").exists()
    box = app.roots.state_root / "trash" / r["trash_id"]
    meta = json.loads((box / "manifest.json").read_text())
    assert meta["path"] == "src" and meta["who"] == "bea" and meta["files"] == 2
    files = [p for p, _ in (await call(app, "fs.find", {}))["files"]]
    assert "src/a.py" not in files
    assert await call(app, "fs.restore", {"trash_id": r["trash_id"]}) == {"path": "src"}
    assert (tmp_path / "src" / "a.py").read_bytes() == before
    assert (tmp_path / "src" / "deep" / "d.bin").read_bytes() == b"\x00\xff" * 50
    assert not box.exists()
    files = [p for p, _ in (await call(app, "fs.find", {}))["files"]]
    assert "src/a.py" in files


async def test_restore_onto_a_recreated_path_is_taken(app, tmp_path):
    r = await call(app, "fs.delete", {"path": "src/a.py"})
    (tmp_path / "src" / "a.py").write_text("new\n")
    e = await refused(app, "fs.restore", {"trash_id": r["trash_id"]})
    assert e.code == "taken"
    assert (tmp_path / "src" / "a.py").read_text() == "new\n"
    e = await refused(app, "fs.restore", {"trash_id": "../../token"})
    assert e.code == "not_found"


async def test_the_root_itself_cannot_be_deleted(app):
    e = await refused(app, "fs.delete", {"path": "."})
    assert e.code == "forbidden"


def test_purge_drops_an_entry_older_than_30_days_and_keeps_a_newer_one(tmp_path):
    state = tmp_path / "state"
    for name in ("old", "new"):
        (tmp_path / name).write_text(name)
        fs_trash.delete(state, tmp_path / name, name, "bea")
    boxes = {
        json.loads((b / "manifest.json").read_text())["path"]: b
        for b in (state / "trash").iterdir()
    }
    # A box's age is its name's stamp.
    stamp = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y%m%d-%H%M%S")
    old = boxes["old"].rename(boxes["old"].with_name(stamp + boxes["old"].name[15:]))
    assert fs_trash.purge(state) == 1
    assert not old.exists() and boxes["new"].exists()


async def test_a_symlink_out_of_the_root_is_listed_but_not_read(app, escapes):
    names = [e["name"] for e in (await call(app, "fs.list", {}))["entries"]]
    assert "out" in names and "outdir" in names
    e = await refused(app, "fs.read", {"path": "out"})
    assert e.code == "outside_root"
    e = await refused(app, "fs.list", {"dir": "outdir"})
    assert e.code == "outside_root"
    e = await refused(app, "fs.delete", {"path": "out"})
    assert e.code == "outside_root"
    e = await refused(app, "fs.move", {"path": "src/a.py", "to": "outdir/a.py"})
    assert e.code == "outside_root"
    assert (escapes / "secret.txt").read_text() == "outside\n"
