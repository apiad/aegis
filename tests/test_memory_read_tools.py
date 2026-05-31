"""Hermetic tests for memory_read / memory_search MCP tools."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from aegis.tools.decorator import _reset_registry_for_tests as _reset_tools


def _load(monkeypatch, tmp_path: Path):
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
async def test_memory_read_returns_entry_body(tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    m.write_entry(tmp_path, "feedback", "phrasing",
                  "no load-bearing", "Avoid that phrase.")
    out = await m.memory_read(slug="feedback_phrasing")
    assert out["slug"] == "feedback_phrasing"
    assert out["type"] == "feedback"
    assert out["name"] == "phrasing"
    assert out["description"] == "no load-bearing"
    assert "Avoid that phrase." in out["content"]


@pytest.mark.asyncio
async def test_memory_read_missing_raises(tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError):
        await m.memory_read(slug="never-existed")


@pytest.mark.asyncio
async def test_memory_search_scores_by_keywords(tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    m.write_entry(tmp_path, "feedback", "phrasing",
                  "load-bearing phrase ban", "Avoid that phrase.")
    m.write_entry(tmp_path, "user", "name",
                  "Goes by Alex", "Use Alex in writing.")
    hits = await m.memory_search(query="load bearing")
    assert hits[0]["slug"] == "feedback_phrasing"
    assert hits[0]["score"] > 0
    assert "snippet" in hits[0]


@pytest.mark.asyncio
async def test_memory_search_respects_limit(tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    for i in range(15):
        m.write_entry(tmp_path, "fact", f"x{i}",
                      f"fact {i} about widgets", f"widget body {i}")
    hits = await m.memory_search(query="widget", limit=5)
    assert len(hits) == 5


@pytest.mark.asyncio
async def test_memory_search_empty_when_no_matches(tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    m.write_entry(tmp_path, "user", "name", "Goes by Alex", "body")
    hits = await m.memory_search(query="xyzzy-no-match")
    assert hits == []
