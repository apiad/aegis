import json

from aegis.config import Agent, OpenCode
from aegis.drivers.opencode import OpenCodeDriver


def test_extra_env_injects_model():
    agent = Agent(provider=OpenCode(model="opencode/deepseek-v4-flash-free"))
    env = OpenCodeDriver().extra_env(agent)
    payload = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert payload["model"] == "opencode/deepseek-v4-flash-free"


def test_extra_env_empty_when_no_model():
    # A bare opencode agent with empty model injects nothing → opencode
    # keeps its own config default.
    agent = Agent(harness="opencode", model="")
    assert OpenCodeDriver().extra_env(agent) == {}
