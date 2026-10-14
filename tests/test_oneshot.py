"""The one-shot path the recap and /btw share."""

import json

from pydantic import BaseModel

from aegis import oneshot

CONFIG = (
    "default_agent: opus\nagents:\n"
    "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    "  oc: {harness: opencode, model: opencode-go/x, effort: high, permission: full}\n"
)


class Out(BaseModel):
    answer: str
    needs_more: bool = False


def test_load_generator_reads_any_key_and_names_it_in_errors(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    assert oneshot.load_generator(tmp_path, "btw") is None
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "btw: {agent: opus}\n")
    assert oneshot.load_generator(tmp_path, "btw") == {"agent": "opus", "model": "opus"}
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "btw: opus\n")
    assert "btw: {agent: <name>}" in oneshot.load_generator(tmp_path, "btw")["error"]
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "btw: {agent: nobody}\n")
    assert "'nobody'" in oneshot.load_generator(tmp_path, "btw")["error"]
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "btw: {agent: oc}\n")
    assert (
        "a btw needs a claude-code agent"
        in oneshot.load_generator(tmp_path, "btw")["error"]
    )


def test_argv_carries_the_given_schema_and_system_and_puts_the_prompt_last():
    a = oneshot.argv("/bin/claude", "m", Out, "be brief", "--- transcript ---")
    assert a[-2:] == ["--", "--- transcript ---"]
    assert json.loads(a[a.index("--json-schema") + 1])["properties"].keys() == {
        "answer",
        "needs_more",
    }
    assert a[a.index("--system-prompt") + 1] == "be brief"


def test_parse_validates_against_the_given_model():
    env = {
        "structured_output": {"answer": "yes"},
        "total_cost_usd": 0.01,
        "duration_ms": 900,
    }
    value, cost, ms = oneshot.parse(json.dumps(env), Out)
    assert value == Out(answer="yes") and cost == 0.01 and ms == 900
    assert oneshot.parse("not json", Out) == (None, 0.0, 0)
