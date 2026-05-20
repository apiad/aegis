from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import ConfigError, load_queues


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".aegis.py"
    p.write_text(body)
    return p


def test_valid_queues_parse(tmp_path):
    p = _write(tmp_path, """
from aegis import Agent
agents = {
    "claude-impl": Agent(harness="claude-code", model="opus",
                         effort="high", permission="auto"),
    "claude-review": Agent(harness="claude-code", model="sonnet",
                           effort="medium", permission="read"),
}
default_agent = "claude-impl"
queues = {
    "impl":   {"agent": "claude-impl",   "max_parallel": 2},
    "review": {"agent": "claude-review", "max_parallel": 4},
}
""")
    qs = load_queues(p)
    assert sorted(qs) == ["impl", "review"]
    assert qs["impl"].agent_profile == "claude-impl"
    assert qs["impl"].max_parallel == 2
    assert qs["review"].agent_profile == "claude-review"


def test_no_queues_dict_returns_empty(tmp_path):
    p = _write(tmp_path, """
from aegis import Agent
agents = {"x": Agent(harness="claude-code", model="opus",
                     effort="high", permission="auto")}
default_agent = "x"
""")
    assert load_queues(p) == {}


def test_unknown_agent_profile_fails_loud(tmp_path):
    p = _write(tmp_path, """
from aegis import Agent
agents = {"x": Agent(harness="claude-code", model="opus",
                     effort="high", permission="auto")}
default_agent = "x"
queues = {"impl": {"agent": "ghost", "max_parallel": 1}}
""")
    with pytest.raises(ConfigError) as ei:
        load_queues(p)
    assert "impl" in str(ei.value) and "ghost" in str(ei.value)


def test_missing_required_keys_fails_loud(tmp_path):
    p = _write(tmp_path, """
from aegis import Agent
agents = {"x": Agent(harness="claude-code", model="opus",
                     effort="high", permission="auto")}
default_agent = "x"
queues = {"impl": {"agent": "x"}}      # missing max_parallel
""")
    with pytest.raises(ConfigError) as ei:
        load_queues(p)
    assert "max_parallel" in str(ei.value) and "impl" in str(ei.value)


def test_negative_max_parallel_fails_loud(tmp_path):
    p = _write(tmp_path, """
from aegis import Agent
agents = {"x": Agent(harness="claude-code", model="opus",
                     effort="high", permission="auto")}
default_agent = "x"
queues = {"impl": {"agent": "x", "max_parallel": 0}}
""")
    with pytest.raises(ConfigError) as ei:
        load_queues(p)
    assert "max_parallel" in str(ei.value) and ">= 1" in str(ei.value)
