"""Hermetic tests for memory_add / memory_replace / memory_remove tools."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from aegis.hooks.decorator import _reset_registry_for_tests as _reset_hooks
from aegis.tools.decorator import _reset_registry_for_tests as _reset_tools


@pytest.fixture(autouse=True)
def _isolate_registries():
    _reset_hooks(); _reset_tools()
    yield
    _reset_hooks(); _reset_tools()


def _load(monkeypatch, tmp_path: Path):
    _reset_hooks()
    _reset_tools()
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / "memory_system.py"
    spec = importlib.util.spec_from_file_location("_test_memory_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_memory_system"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_memory_add_writes_entry_and_index(tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    out = await m.memory_add(type="feedback", name="phrasing",
                             description="no load-bearing", content="avoid it")
    assert out["slug"] == "feedback_phrasing"
    assert Path(out["path"]).exists()
    index = (tmp_path / m.MEMORY_SUBDIR / "MEMORY.md").read_text()
    assert "[phrasing](entries/feedback_phrasing.md) — no load-bearing" in index


@pytest.mark.asyncio
async def test_memory_add_rejects_duplicate(tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    await m.memory_add(type="user", name="x", description="d", content="c")
    with pytest.raises(FileExistsError):
        await m.memory_add(type="user", name="x", description="d2", content="c2")


@pytest.mark.asyncio
async def test_memory_replace_updates_content_and_timestamp(
        tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    await m.memory_add(type="fact", name="cron",
                       description="3am", content="initial")
    out = await m.memory_replace(slug="fact_cron", content="updated body")
    e = m.read_entry(tmp_path, "fact_cron")
    assert e.content.strip() == "updated body"
    assert e.created != e.updated  # timestamp moved forward


@pytest.mark.asyncio
async def test_memory_replace_updates_description_and_index(
        tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    await m.memory_add(type="user", name="name",
                       description="old desc", content="body")
    await m.memory_replace(slug="user_name", description="new desc")
    index = (tmp_path / m.MEMORY_SUBDIR / "MEMORY.md").read_text()
    assert "new desc" in index
    assert "old desc" not in index


@pytest.mark.asyncio
async def test_memory_remove_deletes_file_and_index_line(
        tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    await m.memory_add(type="user", name="name", description="d", content="c")
    out = await m.memory_remove(slug="user_name")
    assert out == {"slug": "user_name", "removed": True}
    assert not (tmp_path / m.ENTRIES_SUBDIR / "user_name.md").exists()
    assert "user_name" not in (tmp_path / m.MEMORY_SUBDIR / "MEMORY.md").read_text()
