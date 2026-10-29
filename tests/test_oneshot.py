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
    assert oneshot.load_generator(tmp_path, "btw") == {
        "agent": "opus",
        "harness": "claude-code",
        "model": "opus",
    }
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "btw: opus\n")
    assert "btw: {agent: <name>}" in oneshot.load_generator(tmp_path, "btw")["error"]
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "btw: {agent: nobody}\n")
    assert "'nobody'" in oneshot.load_generator(tmp_path, "btw")["error"]
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "btw: {agent: oc}\n")
    assert oneshot.load_generator(tmp_path, "btw") == {
        "agent": "oc",
        "harness": "opencode",
        "model": "opencode-go/x",
    }


def test_a_generator_on_a_harness_without_one_shot_calls_is_refused(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  cur: {harness: cursor, model: fake-pro, effort: medium, permission: read}\nrecap: {agent: cur}\n"
    )
    got = oneshot.load_generator(tmp_path, "recap")
    assert got is not None and "cursor" in got["error"]


def test_argv_carries_the_given_schema_and_system_and_never_the_prompt():
    a = oneshot.argv("/bin/claude", "m", Out, "be brief")
    assert "--" not in a and a[-1] == "--strict-mcp-config"
    assert json.loads(a[a.index("--json-schema") + 1])["properties"].keys() == {
        "answer",
        "needs_more",
    }
    assert a[a.index("--system-prompt") + 1] == "be brief"


def test_argv_uses_open_code_run_in_read_only_plan_mode():
    a = oneshot.argv(
        "/bin/opencode", "provider/model", Out, "be brief", harness="opencode"
    )
    assert a[:2] == ["/bin/opencode", "run"]
    assert (
        "--standalone" in a and "--format" in a and a[a.index("--format") + 1] == "json"
    )
    assert a[a.index("--agent") + 1] == "plan"
    assert "be brief" not in a  # no message argument: run reads stdin


def test_argv_uses_codex_exec_in_a_read_only_ephemeral_session():
    a = oneshot.argv("/bin/codex", "openai/model", Out, "be brief", harness="codex")
    assert a[:2] == ["/bin/codex", "exec"]
    assert a[a.index("--model") + 1] == "model"
    assert "--ephemeral" in a and "--json" in a
    assert a[a.index("--sandbox") + 1] == "read-only"
    assert a[-1] == "-" and "be brief" not in a


def test_the_message_on_stdin_carries_the_system_prompt_where_no_flag_does():
    assert oneshot.message("be brief", "context") == "context"
    for h in ("opencode", "codex"):
        assert oneshot.message("be brief", "context", h) == "be brief\n\ncontext"


def test_parse_validates_against_the_given_model():
    env = {
        "structured_output": {"answer": "yes"},
        "total_cost_usd": 0.01,
        "duration_ms": 900,
    }
    value, cost, ms = oneshot.parse(json.dumps(env), Out)
    assert value == Out(answer="yes") and cost == 0.01 and ms == 900
    assert oneshot.parse("not json", Out) == (None, 0.0, 0)


def test_parse_accepts_newline_delimited_harness_events():
    stdout = "\n".join(
        [
            json.dumps({"type": "step_start"}),
            json.dumps(
                {
                    "type": "text",
                    "part": {"text": '{"answer":"yes","needs_more":false}'},
                }
            ),
            json.dumps({"type": "step_finish", "cost": 0.02, "duration": 700}),
        ]
    )
    value, cost, ms = oneshot.parse(stdout, Out)
    assert value == Out(answer="yes") and cost == 0.02 and ms == 700
