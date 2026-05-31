"""Hermetic tests for memory_system frontmatter I/O + index helpers."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_memory_system():
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
