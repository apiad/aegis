"""CanvasManager — state substrate, file I/O, ledger, subscribers."""
from __future__ import annotations

import asyncio
import json

import pytest

from aegis.canvas.manager import (
    CanvasError,
    CanvasManager,
    CanvasNameBound,
    CanvasNotOpen,
)


def _mgr(tmp_path):
    return CanvasManager(state_dir=tmp_path / ".aegis" / "state")


# ---------- open ----------

@pytest.mark.asyncio
async def test_open_creates_empty_file_and_meta(tmp_path):
    mgr = _mgr(tmp_path)
    file = tmp_path / "report.md"
    info = await mgr.open("report-q3", str(file))
    assert info.name == "report-q3"
    assert info.file == str(file)
    assert info.sections == []
    assert file.exists() and file.read_text() == ""
    meta = json.loads(
        (tmp_path / ".aegis" / "state" / "canvases" / "report-q3" /
         "meta.json").read_text())
    assert meta["name"] == "report-q3"
    assert meta["file"] == str(file)


@pytest.mark.asyncio
async def test_open_idempotent_returns_existing(tmp_path):
    mgr = _mgr(tmp_path)
    f = tmp_path / "r.md"
    await mgr.open("r", str(f))
    info = await mgr.open("r")  # no file arg on re-open
    assert info.name == "r"


@pytest.mark.asyncio
async def test_open_rejects_rebinding_to_different_file(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("r", str(tmp_path / "a.md"))
    with pytest.raises(CanvasNameBound):
        await mgr.open("r", str(tmp_path / "b.md"))


@pytest.mark.asyncio
async def test_open_without_file_arg_on_first_open_errors(tmp_path):
    mgr = _mgr(tmp_path)
    with pytest.raises(CanvasNotOpen):
        await mgr.open("ghost")


@pytest.mark.asyncio
async def test_open_preserves_existing_file_content(tmp_path):
    mgr = _mgr(tmp_path)
    f = tmp_path / "r.md"
    f.write_text("## intro\nhello\n")
    info = await mgr.open("r", str(f))
    assert [s.name for s in info.sections] == ["intro"]
    assert info.sections[0].lines == 1


# ---------- write_section ----------

@pytest.mark.asyncio
async def test_write_section_creates_and_persists(tmp_path):
    mgr = _mgr(tmp_path)
    f = tmp_path / "r.md"
    await mgr.open("r", str(f))
    res = await mgr.write_section("r", "intro", "hello\nworld", writer="alice")
    assert res.section == "intro"
    assert res.op == "write"
    assert res.added == 2 and res.removed == 0
    assert "## intro" in f.read_text()
    assert "hello\nworld" in f.read_text()


@pytest.mark.asyncio
async def test_write_section_replaces_existing(tmp_path):
    mgr = _mgr(tmp_path)
    f = tmp_path / "r.md"
    await mgr.open("r", str(f))
    await mgr.write_section("r", "intro", "old", writer="alice")
    res = await mgr.write_section("r", "intro", "new line 1\nnew line 2",
                                  writer="bob")
    assert res.added == 2
    assert res.removed == 1
    text = f.read_text()
    assert "new line 1\nnew line 2" in text
    assert "old" not in text


@pytest.mark.asyncio
async def test_write_rejects_invalid_section_name(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("r", str(tmp_path / "r.md"))
    with pytest.raises(CanvasError):
        await mgr.write_section("r", "bad/name", "x", writer="alice")


@pytest.mark.asyncio
async def test_write_body_rejected_when_file_has_headings(tmp_path):
    mgr = _mgr(tmp_path)
    f = tmp_path / "r.md"
    await mgr.open("r", str(f))
    await mgr.write_section("r", "intro", "i", writer="alice")
    with pytest.raises(CanvasError):
        await mgr.write_section("r", "body", "no", writer="alice")


# ---------- append_to_section ----------

@pytest.mark.asyncio
async def test_append_to_existing_joins_with_newline(tmp_path):
    mgr = _mgr(tmp_path)
    f = tmp_path / "r.md"
    await mgr.open("r", str(f))
    await mgr.write_section("r", "log", "line 1", writer="alice")
    res = await mgr.append_to_section("r", "log", "line 2", writer="bob")
    assert res.op == "append"
    assert res.appended_text == "line 2"
    assert res.added == 1
    text = f.read_text()
    assert "line 1\nline 2" in text


@pytest.mark.asyncio
async def test_append_creates_section_when_missing(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("r", str(tmp_path / "r.md"))
    res = await mgr.append_to_section("r", "log", "first", writer="alice")
    assert res.added == 1
    assert res.new_body == "first"


# ---------- read ----------

@pytest.mark.asyncio
async def test_read_full_returns_file_content(tmp_path):
    mgr = _mgr(tmp_path)
    f = tmp_path / "r.md"
    await mgr.open("r", str(f))
    await mgr.write_section("r", "intro", "hi", writer="alice")
    out = await mgr.read("r")
    assert "## intro" in out


@pytest.mark.asyncio
async def test_read_specific_section(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("r", str(tmp_path / "r.md"))
    await mgr.write_section("r", "intro", "hi", writer="alice")
    body = await mgr.read("r", "intro")
    assert body == "hi"


@pytest.mark.asyncio
async def test_read_missing_section_errors(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("r", str(tmp_path / "r.md"))
    with pytest.raises(CanvasError):
        await mgr.read("r", "ghost")


@pytest.mark.asyncio
async def test_read_unopened_canvas_errors(tmp_path):
    mgr = _mgr(tmp_path)
    with pytest.raises(CanvasNotOpen):
        await mgr.read("ghost")


# ---------- ledger ----------

@pytest.mark.asyncio
async def test_ledger_records_every_write(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("r", str(tmp_path / "r.md"))
    await mgr.write_section("r", "intro", "hi", writer="alice")
    await mgr.append_to_section("r", "intro", "more", writer="bob")
    ledger = (tmp_path / ".aegis" / "state" / "canvases" / "r" /
              "ledger.jsonl").read_text().splitlines()
    assert len(ledger) == 2
    r0 = json.loads(ledger[0])
    r1 = json.loads(ledger[1])
    assert r0["writer"] == "alice" and r0["op"] == "write"
    assert r1["writer"] == "bob" and r1["op"] == "append"


# ---------- subscribers ----------

@pytest.mark.asyncio
async def test_subscribe_all_sections(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("r", str(tmp_path / "r.md"))
    mgr.subscribe("r", "alice")
    assert mgr.subscribers_for_section("r", "intro") == ["alice"]
    assert mgr.subscribers_for_section("r", "data") == ["alice"]


@pytest.mark.asyncio
async def test_subscribe_filtered_sections(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("r", str(tmp_path / "r.md"))
    mgr.subscribe("r", "alice", sections=["data"])
    mgr.subscribe("r", "bob")
    assert mgr.subscribers_for_section("r", "intro") == ["bob"]
    assert sorted(mgr.subscribers_for_section("r", "data")) == ["alice", "bob"]


@pytest.mark.asyncio
async def test_unsubscribe(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("r", str(tmp_path / "r.md"))
    mgr.subscribe("r", "alice")
    mgr.unsubscribe("r", "alice")
    assert mgr.subscribers_for_section("r", "any") == []


# ---------- list ----------

@pytest.mark.asyncio
async def test_list_canvases(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("a", str(tmp_path / "a.md"))
    await mgr.open("b", str(tmp_path / "b.md"))
    names = [c.name for c in mgr.list_canvases()]
    assert sorted(names) == ["a", "b"]


# ---------- recovery from disk ----------

@pytest.mark.asyncio
async def test_manager_recovers_canvases_from_disk(tmp_path):
    mgr1 = _mgr(tmp_path)
    f = tmp_path / "r.md"
    await mgr1.open("r", str(f))
    await mgr1.write_section("r", "intro", "hi", writer="alice")
    # New manager — should see canvas r already registered
    mgr2 = _mgr(tmp_path)
    assert mgr2.is_open("r")
    body = await mgr2.read("r", "intro")
    assert body == "hi"
    # Subscribers are NOT recovered (session-scoped)
    assert mgr2.subscribers("r") == {}


# ---------- concurrency ----------

@pytest.mark.asyncio
async def test_concurrent_writes_serialize(tmp_path):
    mgr = _mgr(tmp_path)
    await mgr.open("r", str(tmp_path / "r.md"))

    # 20 concurrent appends — final content should reflect all of them
    async def one(i):
        await mgr.append_to_section("r", "log", f"line-{i}",
                                    writer=f"w-{i}")

    await asyncio.gather(*[one(i) for i in range(20)])
    body = await mgr.read("r", "log")
    lines = [l for l in body.splitlines() if l.startswith("line-")]
    assert len(lines) == 20
    # All distinct
    assert len(set(lines)) == 20


# ---------- notifier ----------

@pytest.mark.asyncio
async def test_notifier_fires_after_each_write(tmp_path):
    calls = []

    async def notifier(result, state):
        calls.append((result.section, result.op, result.writer))

    mgr = CanvasManager(state_dir=tmp_path / ".aegis" / "state",
                        notifier=notifier)
    await mgr.open("r", str(tmp_path / "r.md"))
    await mgr.write_section("r", "intro", "hi", writer="alice")
    await mgr.append_to_section("r", "intro", "more", writer="bob")
    assert calls == [("intro", "write", "alice"),
                     ("intro", "append", "bob")]
