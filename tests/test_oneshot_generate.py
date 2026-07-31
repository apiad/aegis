"""The one-shot `generate()` seam — structured generation with no session,
no MCP, and no tools.

Shared by `/btw` and the session-titles spec. The live round-trip is
`live`-marked; everything here is hermetic.

Measured on zion 2026-07-31 (claude 2.1.220, haiku), and the reason
`_oneshot_argv` strips what it strips:

    default agentic `claude -p`   21.9s  $0.0633  53,593 in  → refusal
    --system-prompt + --tools ""   8.5s  $0.0044   2,361 in  → correct
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from aegis.config import Agent
from aegis.drivers import get_driver
from aegis.drivers.base import HarnessDriver
from aegis.drivers.oneshot import parse_structured


class Answer(BaseModel):
    answer: str
    needs_more: bool = False


# ---------- the tolerant parser -----------------------------------------

def test_parses_a_bare_json_object():
    got = parse_structured('{"answer": "core/manager.py", "needs_more": false}',
                           Answer)
    assert got == Answer(answer="core/manager.py", needs_more=False)


def test_parses_a_fenced_json_block():
    raw = 'Sure:\n```json\n{"answer": "x", "needs_more": true}\n```\n'
    assert parse_structured(raw, Answer) == Answer(answer="x", needs_more=True)


def test_parses_an_object_embedded_in_prose():
    raw = 'I think {"answer": "y", "needs_more": false} covers it.'
    assert parse_structured(raw, Answer) == Answer(answer="y")


def test_uses_defaults_for_omitted_optional_fields():
    assert parse_structured('{"answer": "z"}', Answer) == Answer(answer="z")


@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "no json here at all",
    '{"needs_more": true}',            # missing the required field
    '{"answer": {"nested": "wrong"}}',  # wrong type
    "{unclosed",
])
def test_gives_up_and_returns_none(raw):
    """Callers treat generate() as best-effort; a bad payload is a None,
    never an exception that reaches the conversation."""
    assert parse_structured(raw, Answer) is None


def test_prefers_the_fenced_block_over_surrounding_prose():
    raw = ('Here {"answer": "decoy"} is my thinking.\n'
           '```json\n{"answer": "real", "needs_more": false}\n```')
    assert parse_structured(raw, Answer).answer == "real"


# ---------- the driver seam ---------------------------------------------

def test_base_driver_declares_no_oneshot_support():
    assert HarnessDriver.supports_oneshot is False


async def test_base_driver_generate_returns_none():
    """The default is best-effort-nothing, so an unimplemented driver
    degrades to 'no side note' rather than an exception."""
    class Bare(HarnessDriver):
        def build_argv(self, agent, cwd, mcp_url, handle): return []
        def session(self, agent, cwd, mcp_url, handle): return None

    assert await Bare().generate(Agent(harness="gemini", model="m"), "/tmp",
                                 Answer, "hi") is None


def test_claude_driver_supports_oneshot():
    assert get_driver("claude-code").supports_oneshot is True


# ---------- what the claude one-shot actually asks for ------------------

def argv_for(**kw) -> list[str]:
    agent = Agent(harness="claude-code", model=kw.pop("model", "haiku"))
    return get_driver("claude-code")._oneshot_argv(
        agent, Answer, ["the window", "the question"], **kw)


def test_oneshot_argv_disables_every_builtin_tool():
    """The default run went agentic — it tried to read files, spent 53k
    input tokens, and answered 'I cannot verify'. An empty --tools is what
    makes this a generator instead of an agent."""
    argv = argv_for()
    assert "--tools" in argv
    assert argv[argv.index("--tools") + 1] == ""


def test_oneshot_argv_replaces_the_system_prompt_rather_than_appending():
    """--append-system-prompt keeps claude's agentic default (and its tool
    schemas). Only --system-prompt replaces it."""
    argv = argv_for()
    assert "--system-prompt" in argv
    assert "--append-system-prompt" not in argv


def test_oneshot_argv_passes_the_json_schema():
    argv = argv_for()
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"].keys() >= {"answer", "needs_more"}
    assert "answer" in schema.get("required", [])


def test_oneshot_argv_asks_for_plain_json_not_a_stream():
    argv = argv_for()
    assert argv[argv.index("--output-format") + 1] == "json"


def test_oneshot_argv_carries_no_mcp_servers():
    """A side note must not reach the aegis MCP plane — no handle, no
    tools, no callbacks."""
    argv = argv_for()
    assert "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == {
        "mcpServers": {}}


def test_oneshot_argv_uses_the_agents_model():
    assert argv_for(model="haiku")[
        argv_for(model="haiku").index("--model") + 1] == "haiku"


def test_oneshot_argv_joins_the_instructions_into_one_prompt():
    argv = argv_for()
    assert "-p" in argv
    prompt = argv[argv.index("-p") + 1]
    assert "the window" in prompt and "the question" in prompt


# ---------- the real thing ----------------------------------------------

@pytest.mark.live
async def test_generate_answers_from_the_window_alone():
    """Needs the real `claude` CLI. Asserts the seam end to end: a window
    in, a validated schema instance out."""
    import shutil
    if not shutil.which("claude"):
        pytest.skip("claude CLI not on PATH")
    window = ("user: we were refactoring the resume flow\n"
              "assistant: I moved resume() into drivers/base.py\n"
              "user: where did the fork guard end up?\n"
              "assistant: core/manager.py, in _forkable()")
    got = await get_driver("claude-code").generate(
        Agent(harness="claude-code", model="haiku"), "/tmp", Answer,
        window, "Question: which file holds the fork guard?")
    assert got is not None
    assert "manager" in got.answer
