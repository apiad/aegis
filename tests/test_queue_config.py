from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import ConfigError, load_queues


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".aegis.yaml"
    p.write_text(body)
    return tmp_path


def test_valid_queues_parse(tmp_path):
    root = _write(tmp_path, """
default_agent: claude-impl
agents:
  claude-impl:
    provider: claude-code
    model: opus
    effort: high
    permission: auto
  claude-review:
    provider: claude-code
    model: sonnet
    effort: medium
    permission: read
queues:
  impl:
    agent: claude-impl
    max_parallel: 2
  review:
    agent: claude-review
    max_parallel: 4
""")
    qs = load_queues(root)
    assert sorted(qs) == ["impl", "review"]
    assert qs["impl"].agent_profile == "claude-impl"
    assert qs["impl"].max_parallel == 2
    assert qs["review"].agent_profile == "claude-review"


def test_no_queues_section_returns_empty(tmp_path):
    root = _write(tmp_path, """
default_agent: x
agents:
  x:
    provider: claude-code
    model: opus
    effort: high
    permission: auto
""")
    assert load_queues(root) == {}


def test_negative_max_parallel_fails_loud(tmp_path):
    root = _write(tmp_path, """
default_agent: x
agents:
  x:
    provider: claude-code
    model: opus
    effort: high
    permission: auto
queues:
  impl:
    agent: x
    max_parallel: 0
""")
    with pytest.raises(ConfigError) as ei:
        load_queues(root)
    assert "max_parallel" in str(ei.value) and ">= 1" in str(ei.value)


def test_unknown_agent_profile_fails_loud(tmp_path):
    root = _write(tmp_path, """
default_agent: x
agents:
  x:
    provider: claude-code
    model: opus
    effort: high
    permission: auto
queues:
  impl:
    agent: ghost
    max_parallel: 1
""")
    with pytest.raises(ConfigError) as ei:
        load_queues(root)
    assert "impl" in str(ei.value) and "ghost" in str(ei.value)
