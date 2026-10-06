from pathlib import Path

import pytest

from aegis.queue.schema import Queue


def test_queue_dataclass_carries_provider_and_model():
    q = Queue(name="impl", agent_profile="opus", max_parallel=2,
              provider="claude-code", model="opus")
    assert q.provider == "claude-code"
    assert q.model == "opus"


def test_queue_defaults_provider_and_model_to_empty():
    q = Queue(name="impl", agent_profile="opus", max_parallel=2)
    assert q.provider == ""
    assert q.model == ""


def test_load_queues_derives_provider_and_model_from_agent(tmp_path):
    """End-to-end: a .aegis.yaml with queues:{...} populates Queue.provider
    and Queue.model from the bound agent profile."""
    from aegis.config import load_queues
    (tmp_path / ".aegis.yaml").write_text("""
default_agent: opus
agents:
  opus:
    provider: claude-code
    model: opus
    effort: high
  haiku:
    provider: claude-code
    model: haiku
    effort: low
queues:
  impl:
    agent: opus
    max_parallel: 2
  fast:
    agent: haiku
    max_parallel: 4
""")
    queues = load_queues(tmp_path)
    assert queues["impl"].provider == "claude-code"
    assert queues["impl"].model == "opus"
    assert queues["fast"].provider == "claude-code"
    assert queues["fast"].model == "haiku"


def test_load_queues_gemini_provider(tmp_path):
    from aegis.config import load_queues
    (tmp_path / ".aegis.yaml").write_text("""
default_agent: g
agents:
  g:
    provider: gemini
    model: gemini-3-pro
queues:
  r:
    agent: g
    max_parallel: 1
""")
    queues = load_queues(tmp_path)
    assert queues["r"].provider == "gemini"
    assert queues["r"].model == "gemini-3-pro"
