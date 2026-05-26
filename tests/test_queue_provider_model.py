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
    """End-to-end: an .aegis.py with queues:{...} populates Queue.provider
    and Queue.model from the bound agent profile."""
    from aegis.config import load_queues
    aegis_py = tmp_path / ".aegis.py"
    aegis_py.write_text("""
from aegis import Agent, ClaudeCode
agents = {
    "opus":  Agent(provider=ClaudeCode(model="opus",  effort="high")),
    "haiku": Agent(provider=ClaudeCode(model="haiku", effort="low")),
}
default_agent = "opus"
queues = {
    "impl":     {"agent": "opus",  "max_parallel": 2},
    "fast":     {"agent": "haiku", "max_parallel": 4},
}
""")
    queues = load_queues(aegis_py)
    assert queues["impl"].provider == "claude-code"
    assert queues["impl"].model == "opus"
    assert queues["fast"].provider == "claude-code"
    assert queues["fast"].model == "haiku"


def test_load_queues_gemini_provider(tmp_path):
    from aegis.config import load_queues
    aegis_py = tmp_path / ".aegis.py"
    aegis_py.write_text("""
from aegis import Agent, GeminiCLI
agents = {"g": Agent(provider=GeminiCLI(model="gemini-3-pro"))}
default_agent = "g"
queues = {"r": {"agent": "g", "max_parallel": 1}}
""")
    queues = load_queues(aegis_py)
    assert queues["r"].provider == "gemini"
    assert queues["r"].model == "gemini-3-pro"
