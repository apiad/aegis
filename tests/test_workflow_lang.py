"""The workflow language: what parses, what the lint refuses and where."""

from aegis.workflow_lang import check_value, parse, render, rows

AGENTS = {"opus", "sonnet"}

GOOD = """
id: two-steps
title: Two steps
say: Write a note, then check it.
root:
  seq:
    - agent: {agent: sonnet, prompt: "Write a note about {{args.topic}}.",
              result: {type: object, properties: {path: {type: string}}, required: [path]}}
      id: write
      say: "Write a note about {{args.topic}}."
    - agent: {agent: opus, prompt: "Check {{write.path}}."}
      say: Check the note.
  say: The whole job.
"""


def errs(text):
    wf, errors = parse(text, AGENTS)
    return wf, [f"{e.path}: {e.message}" for e in errors]


def test_a_good_workflow_parses_and_gets_ids():
    wf, errors = errs(GOOD)
    assert errors == []
    assert wf.id == "two-steps"
    assert [n.id for n in wf.root.children] == ["write", "n2"]
    assert [r["depth"] for r in rows(wf)] == [0, 1, 1]


def test_yaml_that_does_not_parse_is_one_error_at_the_root():
    wf, errors = errs("root: [unclosed")
    assert wf is None and errors[0].startswith("$:")


def test_every_node_needs_say():
    _, errors = errs(GOOD.replace("      say: Check the note.\n", ""))
    assert errors == [
        "$.root.seq[1]: every node needs say: one sentence for the person"
    ]


def test_ids_are_unique():
    _, errors = errs(
        GOOD.replace("say: Check the note.", "say: Check the note.\n      id: write")
    )
    assert "$.root.seq[1]: id write is already used at $.root.seq[0]" in errors


def test_a_reference_must_name_an_earlier_node():
    _, errors = errs(
        GOOD.replace('Write a note about {{args.topic}}.",', 'Write {{later.x}}.",')
    )
    assert any("later is not an earlier node or args" in e for e in errors)


def test_an_unknown_agent_is_refused():
    _, errors = errs(GOOD.replace("agent: opus", "agent: gpt"))
    assert "$.root.seq[1].agent: no agent gpt in .aegis.yaml" in errors


def test_a_kind_from_a_later_slice_is_named():
    _, errors = errs(GOOD.replace("seq:", "par:"))
    assert "$.root: par is not in this version yet" in errors


def test_render_and_check_value():
    assert (
        render(
            "PR {{impl.pr}} for {{args.issue}}",
            {"impl": {"pr": 7}, "args": {"issue": 3}},
        )
        == "PR 7 for 3"
    )
    schema = {
        "type": "object",
        "properties": {"pr": {"type": "integer"}},
        "required": ["pr"],
    }
    assert check_value({"pr": 7}, schema) == []
    assert check_value({"pr": "7"}, schema) == ["$.pr: expected integer"]
    assert check_value({}, schema) == ["$: missing pr"]


def one_agent(extra: str = "", result: str = "") -> str:
    return f"""
id: w
title: T
say: S
root:
  agent: {{agent: opus, prompt: hi{result}}}
  say: Do it.
{extra}"""


def test_node_fields_the_engine_does_not_run_yet_are_refused():
    for field, value in (("retries", "3"), ("deadline", "5m"), ("after", "[x]")):
        wf, errors = errs(one_agent(f"  {field}: {value}\n"))
        assert wf is None
        assert f"$.root.{field}: {field} is not in this version yet" in errors


def test_see_is_a_pointer_and_stays_allowed():
    wf, errors = errs(one_agent("  see: plan.html#a\n"))
    assert errors == [] and wf is not None


def test_a_result_schema_names_only_known_types_and_lists():
    for result, msg in (
        ("{type: strnig}", "$.root.agent.result.type: strnig is not a type"),
        (
            "{type: object, required: n}",
            "$.root.agent.result.required: required is a list",
        ),
        ("{type: string, enum: a}", "$.root.agent.result.enum: enum is a list"),
        (
            "{type: object, properties: {n: {type: intger}}}",
            "$.root.agent.result.properties.n.type: intger is not a type",
        ),
    ):
        wf, errors = errs(one_agent(result=f", result: {result}"))
        assert wf is None and any(e.startswith(msg) for e in errors), (result, errors)


def test_args_is_not_a_node_id():
    wf, errors = errs(
        """
id: w
title: T
say: S
root:
  say: r
  seq:
    - {id: args, say: a, agent: {agent: opus, prompt: hi}}
"""
    )
    assert wf is None
    assert "$.root.seq[0].id: args is reserved for the workflow's arguments" in errors
