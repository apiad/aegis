import pytest

from aegis.config import Agent, ClaudeCode, ConfigError
from aegis.config.persona import read_persona


def test_reads_persona_file(tmp_path):
    p = tmp_path / "reviewer.md"
    p.write_text("You are a terse reviewer.")
    agent = Agent(provider=ClaudeCode(model="opus"))
    agent.prompt = "reviewer.md"
    assert read_persona(agent, str(tmp_path)) == "You are a terse reviewer."


def test_none_when_no_prompt(tmp_path):
    agent = Agent(provider=ClaudeCode(model="opus"))
    assert read_persona(agent, str(tmp_path)) is None


def test_missing_file_fails_loud(tmp_path):
    agent = Agent(provider=ClaudeCode(model="opus"))
    agent.prompt = "nope.md"
    with pytest.raises(ConfigError):
        read_persona(agent, str(tmp_path))


def test_claude_argv_appends_persona(tmp_path):
    from aegis.drivers.claude import ClaudeDriver
    p = tmp_path / "persona.md"
    p.write_text("PERSONA-TEXT")
    agent = Agent(provider=ClaudeCode(model="opus"))
    agent.prompt = "persona.md"
    argv = ClaudeDriver().build_argv(agent, str(tmp_path), "", "handle")
    # two --append-system-prompt: primer first, persona second
    idxs = [i for i, a in enumerate(argv) if a == "--append-system-prompt"]
    assert len(idxs) == 2
    assert argv[idxs[1] + 1] == "PERSONA-TEXT"


def test_claude_argv_no_persona_single_append(tmp_path):
    from aegis.drivers.claude import ClaudeDriver
    agent = Agent(provider=ClaudeCode(model="opus"))
    argv = ClaudeDriver().build_argv(agent, str(tmp_path), "", "handle")
    idxs = [i for i, a in enumerate(argv) if a == "--append-system-prompt"]
    assert len(idxs) == 1
