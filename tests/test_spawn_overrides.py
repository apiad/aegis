from aegis.config import Agent, ClaudeCode, OpenCode
from aegis.core.manager import _overlay_agent


def test_overlay_model_and_effort():
    base = Agent(provider=ClaudeCode(model="opus", effort="high"))
    out = _overlay_agent(base, model="sonnet", effort="low", prompt=None)
    assert out.model == "sonnet"
    assert out.effort.value == "low"
    # driver unchanged
    assert out.harness == "claude-code"


def test_overlay_prompt_only():
    base = Agent(provider=ClaudeCode(model="opus"))
    out = _overlay_agent(base, model=None, effort=None, prompt="p.md")
    assert out.prompt == "p.md"
    assert out.model == "opus"


def test_overlay_model_on_opencode():
    base = Agent(provider=OpenCode(model="opencode/gpt-5.1"))
    out = _overlay_agent(
        base, model="opencode/mimo-v2.5-free", effort=None, prompt=None)
    assert out.model == "opencode/mimo-v2.5-free"
    assert out.harness == "opencode"


def test_overlay_noop_when_all_none():
    base = Agent(provider=ClaudeCode(model="opus", effort="high"))
    out = _overlay_agent(base, model=None, effort=None, prompt=None)
    assert out.model == "opus"
    assert out.effort.value == "high"
