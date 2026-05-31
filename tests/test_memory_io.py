"""Hermetic tests for memory_system frontmatter I/O + index helpers."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from aegis.hooks.decorator import _reset_registry_for_tests as _reset_hooks
from aegis.tools.decorator import _reset_registry_for_tests as _reset_tools


def _load_memory_system():
    _reset_hooks()
    _reset_tools()
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / "memory_system.py"
    spec = importlib.util.spec_from_file_location("_test_memory_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_memory_system"] = module
    spec.loader.exec_module(module)
    return module


def test_write_entry_creates_file_with_frontmatter(tmp_path: Path) -> None:
    m = _load_memory_system()
    root = tmp_path
    path = m.write_entry(
        root=root,
        type_="feedback",
        name="no-load-bearing",
        description="User hates 'load-bearing'",
        content="Avoid the phrase in every draft.",
    )
    assert path == root / ".aegis/memory/entries/feedback_no-load-bearing.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "type: feedback" in text
    assert "name: no-load-bearing" in text
    assert "description: User hates 'load-bearing'" in text
    assert "created:" in text
    assert "updated:" in text
    assert text.rstrip().endswith("Avoid the phrase in every draft.")


def test_read_entry_round_trips(tmp_path: Path) -> None:
    m = _load_memory_system()
    m.write_entry(tmp_path, "fact", "dream-at-3am",
                  "Default cron is 3am", "Body here.")
    entry = m.read_entry(tmp_path, "fact_dream-at-3am")
    assert entry.slug == "fact_dream-at-3am"
    assert entry.type == "fact"
    assert entry.name == "dream-at-3am"
    assert entry.description == "Default cron is 3am"
    assert entry.content.strip() == "Body here."


def test_write_entry_rejects_bad_type(tmp_path: Path) -> None:
    m = _load_memory_system()
    with pytest.raises(ValueError, match="invalid type"):
        m.write_entry(tmp_path, "bogus", "x", "d", "c")


def test_write_entry_kebabs_the_name(tmp_path: Path) -> None:
    m = _load_memory_system()
    path = m.write_entry(tmp_path, "user", "Alex Likes Spanish",
                         "d", "c")
    assert path.name == "user_alex-likes-spanish.md"


def test_list_entries_returns_all(tmp_path: Path) -> None:
    m = _load_memory_system()
    m.write_entry(tmp_path, "user", "name", "Goes by Alex", "body")
    m.write_entry(tmp_path, "feedback", "phrasing", "no load-bearing", "body")
    slugs = sorted(e.slug for e in m.list_entries(tmp_path))
    assert slugs == ["feedback_phrasing", "user_name"]


def test_list_entries_skips_malformed(tmp_path: Path) -> None:
    m = _load_memory_system()
    m.write_entry(tmp_path, "user", "good", "d", "c")
    bad = tmp_path / m.ENTRIES_SUBDIR / "bad.md"
    bad.write_text("no frontmatter here\n", encoding="utf-8")
    slugs = [e.slug for e in m.list_entries(tmp_path)]
    assert slugs == ["user_good"]


def test_rebuild_index_writes_memory_md(tmp_path: Path) -> None:
    m = _load_memory_system()
    m.write_entry(tmp_path, "user", "name", "Goes by Alex", "body")
    m.write_entry(tmp_path, "fact", "cron", "Dream at 3am", "body")
    m.rebuild_index(tmp_path)
    text = (tmp_path / m.MEMORY_SUBDIR / "MEMORY.md").read_text(encoding="utf-8")
    assert "# Memory index" in text
    assert "## Index" in text
    assert "[name](entries/user_name.md) — Goes by Alex" in text
    assert "[cron](entries/fact_cron.md) — Dream at 3am" in text


def test_rebuild_index_empty_when_no_entries(tmp_path: Path) -> None:
    m = _load_memory_system()
    m.rebuild_index(tmp_path)
    text = (tmp_path / m.MEMORY_SUBDIR / "MEMORY.md").read_text(encoding="utf-8")
    assert "## Index" in text
    assert text.count("\n- [") == 0
