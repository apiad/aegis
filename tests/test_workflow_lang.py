"""The workflow language: what parses, what the lint refuses and where."""

from aegis.workflow_lang import check_value, item_keys, parse, render, rows, value_of

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
    _, errors = errs(GOOD.replace("seq:", "loop:"))
    assert "$.root: loop is not in this version yet" in errors


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
    for field, value in (("retries", "3"), ("deadline", "5m")):
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


TEAMS = """
id: teams
title: Teams
say: Two teams at once.
limits: {agents: 3, heavy: 1}
root:
  par:
    - map: {over: "{{args.images}}", as: s, cap: 1,
            do: {agent: {agent: opus, prompt: "Build {{s.id}}.", class: heavy}, id: img, say: "Build {{s.id}}."}}
      id: images
      say: Images, one at a time.
    - map: {over: [a, b], as: m, do: {agent: {agent: sonnet, prompt: "Mock {{m}} on {{images.base.img}}."}, id: mk, say: "Mock {{m}}."}}
      id: mock
      after: [images.base]
      say: Mocks, once the base image is built.
  cap: 2
  say: Both teams.
"""


def test_par_map_after_and_limits_parse():
    wf, errors = errs(TEAMS)
    assert errors == []
    assert wf.limits == {"agents": 3, "heavy": 1}
    images, mock = wf.root.children
    assert (images.kind, images.body["cap"], images.children[0].maps) == (
        "map",
        1,
        ("images",),
    )
    assert mock.after == ["images.base"] and wf.root.body == {"cap": 2}


def test_rows_draw_a_map_once_before_it_starts_and_per_item_after():
    wf, _ = errs(TEAMS)
    before = rows(wf)
    assert [(r["id"], r.get("each", False)) for r in before][:3] == [
        ("n1", False),
        ("images", False),
        ("img", True),
    ]
    items = {"images": [["base", {"id": "base"}], ["api", {"id": "api"}]]}
    after = rows(wf, lambda path: items.get(path))
    assert [r["id"] for r in after][:4] == [
        "n1",
        "images",
        "images.base.img",
        "images.api.img",
    ]
    assert after[2]["node"] == "img" and after[2]["depth"] == 2


def test_map_fields_are_checked_at_their_paths():
    for old, new, msg in (
        (
            'over: "{{args.images}}"',
            "over: 3",
            "$.root.par[0].map.over: over is a list, or one {{reference}} to a list",
        ),
        (
            'over: "{{args.images}}"',
            'over: "x {{args.images}}"',
            "$.root.par[0].map.over: over is a list",
        ),
        ("as: s,", "as: Bad,", "$.root.par[0].map.as: as names the item"),
        ("cap: 1,", "cap: 0,", "$.root.par[0].map.cap: cap is a positive integer"),
        ("cap: 1,", "cap: 5,", "$.root.par[0].map.cap: cap 5 is above limits.agents 3"),
        (
            "as: s, cap: 1,",
            "as: img, cap: 1,",
            "$.root.par[0].map.as: img is a node id",
        ),
    ):
        wf, errors = errs(TEAMS.replace(old, new, 1))
        assert wf is None and any(e.startswith(msg) for e in errors), (new, errors)


def test_cap_sits_only_beside_par_and_class_is_heavy():
    _, errors = errs(
        TEAMS.replace("      id: images\n", "      id: images\n      cap: 2\n")
    )
    assert (
        "$.root.par[0].cap: cap sits beside par; a map's cap goes inside map" in errors
    )
    _, errors = errs(TEAMS.replace("class: heavy", "class: huge"))
    assert "$.root.par[0].map.do.agent.class: class is heavy or absent" in errors


def test_limits_are_positive_and_heavy_fits_in_agents():
    for limits, msg in (
        ("{agents: 0}", "$.limits: limits is {agents: N, heavy: M}"),
        ("{agent: 3}", "$.limits: limits is {agents: N, heavy: M}"),
        ("{agents: 2, heavy: 3}", "$.limits.heavy: heavy is at most agents"),
    ):
        _, errors = errs(TEAMS.replace("{agents: 3, heavy: 1}", limits))
        assert any(e.startswith(msg) for e in errors), (limits, errors)


def test_an_after_that_can_never_be_met_is_refused():
    seq = """
id: w
title: T
say: S
root:
  seq:
    - {id: a, say: A, after: [b], agent: {agent: opus, prompt: hi}}
    - {id: b, say: B, agent: {agent: opus, prompt: hi}}
  say: R
"""
    _, errors = errs(seq)
    assert (
        "$.root.seq[0].after[0]: waiting on b closes a cycle: b cannot finish before this node starts"
        in errors
    )
    _, errors = errs(
        seq.replace("seq:", "par:").replace(
            "{id: b, say: B,", "{id: b, say: B, after: [a],"
        )
    )
    assert any(
        e.endswith("closes a cycle: a cannot finish before this node starts")
        for e in errors
    )
    _, errors = errs(seq.replace("after: [b]", "after: [n1]"))
    assert (
        "$.root.seq[0].after[0]: n1 contains this node, so it cannot finish first"
        in errors
    )
    _, errors = errs(seq.replace("after: [b]", "after: [nope]"))
    assert "$.root.seq[0].after[0]: nope is not a node" in errors
    _, errors = errs("""
id: w
title: T
say: S
root:
  seq:
    - {id: s1, say: A, seq: [{say: B, agent: {agent: opus, prompt: hi}}]}
    - {id: c, say: C, after: [s1.x], agent: {agent: opus, prompt: hi}}
  say: R
""")
    assert (
        "$.root.seq[1].after[0]: s1 is a seq: only a map has items and only an agent has outputs"
        in errors
    )
    _, errors = errs(TEAMS.replace("after: [images.base]", "after: [img]"))
    assert (
        "$.root.par[1].after[0]: img is inside map images; wait on images or one of its items"
        in errors
    )


def test_a_reference_to_a_branch_beside_needs_after():
    _, errors = errs(TEAMS.replace("after: [images.base]", "after: []"))
    assert any(
        "images may not have finished before this node starts" in e for e in errors
    )
    _, errors = errs(
        TEAMS.replace("Mock {{m}} on {{images.base.img}}.", "Mock on {{img.x}}.")
    )
    assert any("img is inside map images" in e for e in errors)
    assert errs(TEAMS)[1] == []  # the after makes the same reference sound


def test_an_output_named_in_after_is_readable_and_listed_as_waited():
    wf, errors = errs("""
id: w
title: T
say: S
root:
  par:
    - {id: probe, say: Probe., agent: {agent: opus, prompt: hi}}
    - {id: use, say: "Use {{probe.timers}}.", after: [probe.timers], agent: {agent: opus, prompt: hi}}
  say: R
""")
    assert errors == [] and wf.waited == {"probe": ["timers"]}


def test_what_slice_2_does_not_run_is_still_named():
    for old, new, msg in (
        ("par:", "loop:", "$.root: loop is not in this version yet"),
        (
            "class: heavy",
            "session: main",
            "$.root.par[0].map.do.agent.session: session is not in this version yet",
        ),
        (
            "limits:",
            "blocks: {}\nlimits:",
            "$.blocks: blocks is not in this version yet",
        ),
    ):
        _, errors = errs(TEAMS.replace(old, new, 1))
        assert msg in errors, (new, errors)


def test_value_of_and_item_keys():
    scope = {"args": {"xs": [1, 2]}}
    assert value_of("{{args.xs}}", scope) == [1, 2]
    assert value_of([3], scope) == [3]
    assert item_keys([{"id": "base"}, "x", {"id": "api"}]) == ["base", "1", "api"]
    assert item_keys([{"id": "a"}, {"id": "a"}]) == "two items have the key a"
    assert (
        item_keys([{"id": "a b"}])
        == "item 0's id 'a b' is not letters, digits, - and _"
    )
