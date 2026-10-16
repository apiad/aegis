"""The workflow language: what parses, what the lint refuses and where."""

import pytest

from aegis.workflow_lang import (
    KINDS,
    RUNS,
    check_value,
    duration,
    edit_errors,
    item_keys,
    parse,
    render,
    rows,
    template_key,
    value_of,
    with_defaults,
)

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


def test_every_kind_the_spec_names_runs():
    assert RUNS == frozenset(KINDS)


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


def test_an_agent_takes_retries_and_a_deadline():
    for field, value in (("retries", "3"), ("deadline", "5m")):
        wf, errors = errs(one_agent(f"  {field}: {value}\n"))
        assert errors == [] and wf is not None


def test_see_is_a_file_path_kept_on_the_node_and_its_row():
    wf, errors = errs(one_agent("  see: docs/plan.html#slice-1\n"))
    assert errors == [] and wf.root.see == "docs/plan.html#slice-1"
    (row,) = rows(wf)
    assert row["see"] == "docs/plan.html#slice-1"


def test_see_must_be_a_fixed_path():
    for value, msg in (
        ("[a, b]", "see is a path to a file, with an optional #fragment or :line"),
        (
            '"https://example.com/plan"',
            "see is a path to a file, relative to the run's folder or absolute, not a URL",
        ),
        # Fixed when approved: no step's result may choose the file later.
        ('"plan.html#{{args.slice}}"', "see is a fixed path, with no {{...}} in it"),
        ('"{{impl.path}}"', "see is a fixed path, with no {{...}} in it"),
    ):
        _, got = errs(one_agent(f"  see: {value}\n"))
        assert any(msg in e and e.startswith("$.root") for e in got), (value, got)


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
limits: {total: 3, opus: 1}
root:
  par:
    - map: {over: "{{args.images}}", as: s, cap: 1,
            do: {agent: {agent: opus, prompt: "Build {{s.id}}."}, id: img, say: "Build {{s.id}}."}}
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
    assert wf.limits == {"total": 3, "opus": 1}
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
    assert after[2]["node"] == "images/img" and after[2]["depth"] == 2


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
        ("cap: 1,", "cap: 5,", "$.root.par[0].map.cap: cap 5 is above limits.total 3"),
        (
            "as: s, cap: 1,",
            "as: img, cap: 1,",
            "$.root.par[0].map.as: img is a node id",
        ),
    ):
        wf, errors = errs(TEAMS.replace(old, new, 1))
        assert wf is None and any(e.startswith(msg) for e in errors), (new, errors)


def test_cap_sits_only_beside_par_and_class_is_gone():
    _, errors = errs(
        TEAMS.replace("      id: images\n", "      id: images\n      cap: 2\n")
    )
    assert (
        "$.root.par[0].cap: cap sits beside par; a map's cap goes inside map" in errors
    )
    _, errors = errs(
        TEAMS.replace('"Build {{s.id}}."}', '"Build {{s.id}}.", class: heavy}')
    )
    assert "$.root.par[0].map.do.agent.class: class is not an agent field" in errors


def test_limits_are_positive_and_name_a_total_and_agents():
    for limits, msg in (
        ("{total: 0}", "$.limits: limits is {total: N, <agent>: M}"),
        ("{total: 3, opus: x}", "$.limits: limits is {total: N, <agent>: M}"),
        ("{agents: 3}", "$.limits.agents: agents is not an agent in .aegis.yaml"),
        (
            "{total: 3, heavy: 1}",
            "$.limits.heavy: heavy is not an agent in .aegis.yaml",
        ),
        ("{total: 3, opus: 4}", "$.limits.opus: opus 4 is above total 3"),
    ):
        _, errors = errs(TEAMS.replace("{total: 3, opus: 1}", limits))
        assert any(e.startswith(msg) for e in errors), (limits, errors)
    wf, errors = errs(TEAMS.replace("{total: 3, opus: 1}", "{sonnet: 1}"))
    assert errors == [] and wf.limits == {"sonnet": 1}


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


def test_session_main_takes_no_agent():
    base = (
        "id: m\ntitle: M\nsay: M.\nroot: {{agent: {{{body}}}, id: tick, say: Tick.}}\n"
    )
    assert errs(base.format(body="session: main, prompt: check the deploy"))[1] == []
    _, got = errs(base.format(body="session: main, agent: opus, prompt: p"))
    assert (
        "$.root.agent.agent: agent does not go with session: main: the main agent runs it"
        in got
    )
    wf, _ = errs(base.format(body="session: main, prompt: check the deploy"))
    (row,) = rows(wf)
    assert row["main"] is True and row["agent"] is None


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


def test_two_maps_may_name_their_item_alike():
    """The spec's example names every map's item s: each reference reads
    the item of its own enclosing map, innermost first."""
    text = """
id: alike
title: Alike
say: Two maps, both as s.
args: {type: object, properties: {a: {type: array}, b: {type: array}}}
root:
  par:
    - map: {over: "{{args.a}}", as: s, do: {agent: {agent: opus, prompt: "A {{s.id}}."}, id: one, say: "A {{s.id}}."}}
      id: first
      say: The first map.
    - map:
        over: "{{args.b}}"
        as: s
        do:
          map: {over: "{{s.parts}}", as: s, do: {agent: {agent: opus, prompt: "B {{s}}."}, id: two, say: "B {{s}}."}}
          id: inner
          say: "Parts of {{s.id}}."
      id: second
      say: The second map.
  say: Both.
"""
    assert errs(text)[1] == []


def test_every_maps_as_is_checked_against_node_ids():
    text = """
id: clash
title: Clash
say: Both maps name their item after a node.
root:
  par:
    - map: {over: [1], as: two, do: {agent: {agent: opus, prompt: "x"}, id: one, say: x}}
      id: first
      say: First.
    - map: {over: [1], as: two, do: {agent: {agent: opus, prompt: "y"}, id: two, say: y}}
      id: second
      say: Second.
  say: Both.
"""
    got = errs(text)[1]
    for i in (0, 1):
        assert (
            f"$.root.par[{i}].map.as: two is a node id; name the item otherwise" in got
        )


def test_the_specs_example_lints():
    """The example in spec §04, read from the spec itself, gate and all."""
    import html
    import re
    from pathlib import Path

    spec = Path(__file__).parents[1] / (
        "docs/superpowers/specs/2026-10-15-dynamic-workflows-design.html"
    )
    pre = re.search(r'<h3 id="example">.*?<pre>(.*?)</pre>', spec.read_text(), re.S)
    example = html.unescape(pre.group(1))
    wf, got = errs(example)
    assert wf is not None and got == []
    assert any(n.kind == "gate" for n in wf.by_key.values())


def test_a_reference_reaches_positional_keys_and_dashed_names():
    assert render("{{m.0.one}}", {"m": {"0": {"one": 5}}}) == "5"
    assert render("{{impl-a.pr-url}}", {"impl-a": {"pr-url": "u"}}) == "u"
    assert value_of("{{m.0}}", {"m": {"0": [1]}}) == [1]
    text = """
id: refs
title: Refs
say: Read an item by position, then a dashed name.
root:
  seq:
    - map: {over: [1, 2], as: x, do: {agent: {agent: opus, prompt: "x"}, id: one, say: x}}
      id: m
      say: Each.
    - agent: {agent: opus, prompt: "Read {{m.0.one}} and {{impl-a.x}}."}
      say: "Read {{nothere.x-y}}."
  say: All.
"""
    got = errs(text)[1]
    assert "$.root.seq[1].say: nothere is not an earlier node or args" in got
    assert "$.root.seq[1].agent.prompt: impl-a is not an earlier node or args" in got
    assert not any("m " in e or "m is" in e for e in got), got


def test_a_brace_pair_that_is_not_a_reference_is_refused():
    text = GOOD.replace("Check {{write.path}}.", "Check {{write path}} and {{ }}.")
    assert errs(text)[1] == [
        "$.root.seq[1].agent.prompt: {{write path}}: unexpected 'path'",
        "$.root.seq[1].agent.prompt: {{}}: an empty expression",
    ]


def test_a_result_property_may_not_take_an_awaited_outputs_name():
    text = """
id: shadow
title: Shadow
say: The result declares the output's name.
root:
  par:
    - agent: {agent: opus, prompt: "Probe.",
              result: {type: object, properties: {timers: {type: array}, n: {type: integer}}}}
      id: probe
      say: Probe.
    - agent: {agent: opus, prompt: "Use {{probe.timers}}."}
      after: [probe.timers]
      say: Use them.
  say: Both.
"""
    assert errs(text)[1] == [
        "$.root.par[0].agent.result.properties.timers: timers is an early output "
        "an after waits on; name the result's property otherwise"
    ]


CAPPED = """
id: capx
title: Cap
say: A cap of one.
root:
  par:
    - seq:
        - {id: a1, say: First., agent: {agent: opus, prompt: x}}
        - {id: a2, after: [AFTER], say: Second., agent: {agent: opus, prompt: x}}
      id: left
      say: Left.
    - {id: b, say: Bee., agent: {agent: opus, prompt: x}}
  cap: CAP
  say: Both.
"""


def test_an_after_across_branches_of_a_capped_par_is_refused():
    """The branch that waits holds its slot, so with cap 1 the branch it
    waits on never starts."""
    for after in ("b", "b.o"):
        wf, got = errs(CAPPED.replace("AFTER", after).replace("CAP", "1"))
        assert got == [
            f"$.root.par[0].seq[1].after[0]: {after.split('.')[0]} is in another "
            "branch of the par at $.root, whose cap 1 is below its 2 branches: "
            "this branch holds its slot while it waits, so that one may never "
            "start. Put the after on left, or raise the cap"
        ], got


def test_an_after_across_branches_is_fine_when_every_branch_fits():
    assert errs(CAPPED.replace("AFTER", "b").replace("CAP", "2"))[1] == []
    no_cap = CAPPED.replace("AFTER", "b").replace("  cap: CAP\n", "")
    assert errs(no_cap)[1] == []


def test_a_branch_whose_head_waits_on_a_sibling_holds_no_slot():
    text = CAPPED.replace("AFTER", "b").replace("CAP", "1")
    text = text.replace("      id: left\n", "      id: left\n      after: [b]\n")
    text = text.replace("{id: a2, after: [b], ", "{id: a2, ")
    assert errs(text)[1] == []


def test_an_item_never_waits_on_an_item_of_its_own_map():
    text = """
id: own
title: Own
say: An item waits on another.
root:
  map: {over: [{id: a}, {id: b}], as: x, cap: 1, do: {seq: [
          {id: one, say: One., agent: {agent: opus, prompt: x}},
          {id: two, after: [m.a], say: Two., agent: {agent: opus, prompt: x}}],
        say: Each.}}
  id: m
  say: The map.
"""
    assert "m contains this node, so it cannot finish first" in "".join(errs(text)[1])


def test_a_duration_is_days_hours_minutes_seconds_in_order():
    assert duration("20m") == 1200
    assert duration("1h30m") == 5400
    assert duration("2d") == 172800
    assert duration("45s") == 45
    for bad in ("", "0m", "30", "1m1h", "1.5h", "soon", None, 30):
        assert duration(bad) is None


def test_deadline_and_retries_parse_on_nodes_and_the_header():
    text = GOOD.replace("say: The whole job.", "say: The whole job.\n  deadline: 2h")
    text += "deadline: 8h\n"
    text = text.replace(
        "      id: write\n", "      id: write\n      deadline: 20m\n      retries: 2\n"
    )
    wf, errors = errs(text)
    assert errors == []
    assert wf.deadline == 8 * 3600
    write = wf.root.children[0]
    assert (write.deadline, write.retries) == (1200, 2)
    assert wf.root.deadline == 7200


def test_a_bad_deadline_and_retries_are_refused_at_their_path():
    text = GOOD.replace(
        "      id: write\n",
        "      id: write\n      deadline: soon\n      retries: 11\n",
    )
    _, errors = errs(text)
    assert (
        "$.root.seq[0].deadline: a deadline is a duration such as 20m or 1h30m"
        in errors
    )
    assert "$.root.seq[0].retries: retries is a whole number from 0 to 10" in errors


def test_retries_go_on_leaves_only():
    text = GOOD.replace(
        "  say: The whole job.\n", "  say: The whole job.\n  retries: 1\n"
    )
    _, errors = errs(text)
    assert (
        "$.root.retries: retries go on agent, sh and check; repeat a group with loop"
        in errors
    )


LAND = """
id: release
title: Release
say: Three teams.
args: {type: object, properties: {a: {type: array}, b: {type: array}}}
defs:
  pr: {type: object, properties: {pr: {type: integer}}, required: [pr]}
root:
  par:
    - map: {over: "{{args.a}}", as: s, do: {ref: land}}
      id: ingest
      say: Ingest.
    - map: {over: "{{args.b}}", as: s, do: {ref: land}}
      id: images
      say: Images.
  say: Both teams.
blocks:
  land:
    seq:
      - agent: {agent: sonnet, prompt: "Implement {{s.id}}.", result: {$ref: "#/defs/pr"}}
        id: impl
        say: "Implement {{s.id}}."
      - agent: {agent: opus, prompt: "Review PR {{impl.pr}}."}
        id: review
        say: Review it.
    say: "{{s.id}}: implement, review."
"""


def test_a_block_used_in_two_maps_has_its_ids_in_each():
    wf, errors = errs(LAND)
    assert errors == []
    assert sorted(k for k in wf.by_key if k.endswith(("impl", "review"))) == [
        "images/impl",
        "images/review",
        "ingest/impl",
        "ingest/review",
    ]
    assert wf.by_key["ingest/impl"].body["result"] == {
        "type": "object",
        "properties": {"pr": {"type": "integer"}},
        "required": ["pr"],
    }
    assert template_key("images.base.impl") == "images/impl"
    assert template_key("outer.k.inner.j.leaf") == "outer/inner/leaf"


def test_a_ref_without_say_takes_the_blocks():
    wf, _ = errs(LAND)
    assert wf.by_key["ingest"].children[0].say == "{{s.id}}: implement, review."


def test_a_block_used_twice_in_one_scope_is_refused():
    text = LAND.replace(
        "root:\n  par:\n",
        "root:\n  par:\n    - {ref: land, say: Once.}\n    - {ref: land, say: Twice.}\n",
    )
    _, errors = errs(text)
    assert any("id impl is already used" in e and "block land" in e for e in errors)


def test_a_ref_to_nothing_a_block_that_uses_itself_and_a_block_nobody_uses():
    _, errors = errs(
        LAND.replace(
            "do: {ref: land}}\n      id: ingest", "do: {ref: lnd}}\n      id: ingest"
        )
    )
    assert any(e.endswith("ref: no block lnd in blocks") for e in errors)
    _, errors = errs(LAND + "  spare: {agent: {agent: opus, prompt: x}, say: x}\n")
    assert "$.blocks.spare: block spare is never used" in errors
    loop = LAND.replace(
        "        say: Review it.\n", "        say: Review it.\n      - {ref: land}\n"
    )
    _, errors = errs(loop)
    assert any("block land uses itself" in e for e in errors)


def test_defs_resolve_and_refuse_what_they_cannot():
    _, errors = errs(LAND.replace("#/defs/pr", "#/defs/nope"))
    assert any("no def nope in defs" in e for e in errors)
    _, errors = errs(
        LAND.replace('{$ref: "#/defs/pr"}', '{$ref: "#/defs/pr", type: object}')
    )
    assert any(
        '$ref is alone in its mapping and reads "#/defs/<name>"' in e for e in errors
    )
    looped = LAND.replace(
        "  pr: {type: object", '  pr: {$ref: "#/defs/pr"}\n  pr2: {type: object'
    )
    _, errors = errs(looped)
    assert any("def pr refers to itself" in e for e in errors)


def test_a_name_reads_the_innermost_scope_that_has_it():
    text = LAND.replace(
        "root:\n  par:\n",
        "root:\n  par:\n    - {agent: {agent: opus, prompt: top}, id: impl, say: Top.}\n",
    )
    wf, errors = errs(text)
    # impl at the top and impl inside each map do not clash: different scopes.
    # review's {{impl.pr}} reads its own item's impl, not the top one beside
    # the maps in the par, which may not have finished.
    assert errors == []
    assert {"impl", "ingest/impl", "images/impl"} <= set(wf.by_key)


STEPS = """
id: steps
title: Steps
say: Every kind of step.
root:
  seq:
    - agent: {agent: opus, prompt: "Open a PR.",
              result: {type: object, properties: {pr: {type: integer}}, required: [pr]}}
      id: impl
      say: Open a PR.
    - check: {sh: "gh pr checks {{impl.pr}}", every: 1m}
      deadline: 2h
      retries: 1
      say: Wait for CI.
    - if:
        when: {expr: "impl.pr > 100"}
        then: {sh: "echo big {{impl.pr}}", id: big, say: Say it is big.}
        else: {wait: 1m, say: Pause.}
      id: size
      say: Branch on the PR number.
    - loop:
        max: 3
        until: {judge: {agent: opus, prompt: "Is it approved?", inputs: [fix, impl]}}
        do: {agent: {agent: opus, prompt: "Fix PR {{impl.pr}}."}, id: fix, say: Fix it.}
      id: review
      say: Fix until a judge approves.
    - sh: "gh pr merge {{impl.pr}} --squash"
      say: Merge it.
  say: The whole thing.
"""


def test_every_new_kind_parses():
    wf, errors = errs(STEPS)
    assert errors == []
    kinds = [r["kind"] for r in rows(wf)]
    assert kinds == ["seq", "agent", "check", "if", "sh", "wait", "loop", "agent", "sh"]
    check = wf.root.children[1]
    assert check.body["probe"] == {"sh": "gh pr checks {{impl.pr}}", "every": 60.0}
    assert [c.branch for c in wf.root.children[2].children] == ["then", "else"]
    assert wf.by_key["review/fix"].maps == ("review",)


def test_a_loop_without_max_is_refused():
    _, errors = errs(STEPS.replace("        max: 3\n", ""))
    assert (
        "$.root.seq[3].loop.max: a loop needs max: a whole number from 1 to 1000, or one {{expression}}"
        in errors
    )


def test_a_probe_is_exactly_one_of_sh_expr_and_judge():
    _, errors = errs(
        STEPS.replace('check: {sh: "gh pr checks {{impl.pr}}", every: 1m}', "check: {}")
    )
    assert "$.root.seq[1].check: a probe is one of sh, expr and judge" in errors
    _, errors = errs(STEPS.replace("every: 1m", "every: 1s"))
    assert "$.root.seq[1].check.every: every is a duration of at least 5s" in errors


def test_an_expr_probe_is_checked_like_a_template():
    _, errors = errs(STEPS.replace('"impl.pr > 100"', '"impl.pr > 100 >"'))
    assert any(e.startswith("$.root.seq[2].if.when.expr:") for e in errors)
    _, errors = errs(STEPS.replace('"impl.pr > 100"', '"later.x"'))
    assert any("later is not an earlier node or args" in e for e in errors)


def test_a_reference_into_a_branch_from_outside_it_is_refused():
    text = STEPS.replace('"gh pr merge {{impl.pr}} --squash"', '"echo {{big.code}}"')
    _, errors = errs(text)
    assert any(
        "big is inside the then branch of if size, which may not run" in e
        for e in errors
    )


def test_a_loops_round_is_read_from_outside_through_the_loop():
    _, errors = errs(
        STEPS.replace('"gh pr merge {{impl.pr}} --squash"', '"echo {{fix}}"')
    )
    assert any("fix is inside loop review; read review.fix" in e for e in errors)
    _, errors = errs(
        STEPS.replace('"gh pr merge {{impl.pr}} --squash"', '"echo {{review.fix}}"')
    )
    assert errors == []


def test_a_judge_names_an_agent_and_sound_inputs():
    _, errors = errs(STEPS.replace("{judge: {agent: opus,", "{judge: {agent: nobody,"))
    assert any("no agent nobody in .aegis.yaml" in e for e in errors)
    _, errors = errs(STEPS.replace("inputs: [fix, impl]", "inputs: [merge]"))
    assert any("merge is not an earlier node or args" in e for e in errors)


def test_a_wait_is_a_duration_or_one_expression():
    _, errors = errs(
        STEPS.replace("{wait: 1m, say: Pause.}", "{wait: soon, say: Pause.}")
    )
    assert (
        "$.root.seq[2].if.else.wait: wait is a duration such as 20m, or one {{expression}}"
        in errors
    )
    assert errs(STEPS.replace("{wait: 1m,", '{wait: "{{impl.pr}}s",'))[
        1
    ]  # not one whole expression


def _one_sh(cmd: str, probe: bool = False) -> list[str]:
    import json

    c = json.dumps(cmd)
    node = (
        f"{{check: {{sh: {c}}}, say: Probe.}}" if probe else f"{{sh: {c}, say: Run.}}"
    )
    text = f"id: q\ntitle: Q\nsay: Q.\nargs: {{type: object}}\nroot: {node}\n"
    return errs(text)[1]


def test_a_template_inside_shell_quoting_is_refused():
    # Values arrive through the environment: inside single quotes or a
    # quoted heredoc the reference would stay literal.
    for where, cmd in [
        ("single quotes", "echo '{{args.t}}'"),
        ("a quoted heredoc", "cat <<'EOF'\n{{args.t}}\nEOF"),
    ]:
        for probe in (False, True):
            got = _one_sh(cmd, probe)
            assert len(got) == 1, (cmd, got)
            path = "$.root.check.sh" if probe else "$.root.sh"
            assert got[0].startswith(f"{path}: {{{{args.t}}}} sits inside {where}"), got
            assert "stays literal" in got[0], got


def test_a_bare_template_beside_quoting_lints_clean():
    for cmd in [
        "echo {{args.t}}",
        "echo 'it''s' {{args.t}} \"done\"",
        "echo $(date) {{args.t}} `true`",
        "( cd x && echo {{args.t}} )",
        "echo {{args.t}} # don't quote it",
        "cat <<'EOF'\nplain\nEOF\necho {{args.t}}",
        'echo "a $b {{args.t}} c" `echo {{args.t}}` $(cat {{args.t}})',
        "cat <<EOF\n{{args.t}}\nEOF",
        "cat <<EOF\nplain\nEOF\necho {{args.t}}",
        "test {{args.t or 'a b'}} = x",
        "echo \\' {{args.t}}",
    ]:
        assert _one_sh(cmd) == [], cmd


def test_an_expression_nested_too_deep_is_a_lint_error():
    for expr in ["(" * 400 + "args.x" + ")" * 400, "not " * 2000 + "args.x"]:
        text = f'id: d\ntitle: D\nsay: D.\nroot: {{check: {{expr: "{expr}"}}, say: Deep.}}\n'
        wf, got = errs(text)
        assert wf is None and len(got) == 1, got[:2]
        assert got[0].startswith("$.root.check.expr: ") and "nested" in got[0], got
    ok_ = "(" * 60 + "args.x" + ")" * 60
    text = f'id: d\ntitle: D\nsay: D.\nroot: {{check: {{expr: "{ok_}"}}, say: Deep.}}\n'
    assert errs(text)[1] == []


def test_a_workflow_nested_too_deep_is_a_lint_error():
    node = '{sh: "true", say: Leaf.}'
    for _ in range(600):
        node = f"{{seq: [{node}], say: Group.}}"
    wf, got = errs(f"id: d\ntitle: D\nsay: D.\nroot: {node}\n")
    assert wf is None and got and "too deep" in got[0], got[:1]


def test_a_gate_parses_with_ask_by_and_show():
    wf, errors = errs(
        "id: g\ntitle: G\nsay: G.\nroot:\n  seq:\n"
        '    - {sh: "true", id: a, say: A.}\n'
        '    - {gate: {by: person, ask: "Go on after {{a.code}}?", show: [a, a.out]}, id: ok, say: Ask.}\n'
        "  say: S.\n"
    )
    assert errors == []
    g = wf.by_key["ok"]
    assert g.kind == "gate" and g.body == {
        "by": "person",
        "ask": "Go on after {{a.code}}?",
        "show": ["a", "a.out"],
    }


@pytest.mark.parametrize(
    "gate, path, message",
    [
        (
            "{by: robot, ask: Go?}",
            "$.root.seq[0].gate.by",
            "a gate is answered by main or by person",
        ),
        ("{by: person}", "$.root.seq[0].gate.ask", "a gate needs ask: the question"),
        (
            "{by: main, ask: Go?, show: a}",
            "$.root.seq[0].gate.show",
            "show is a list of references",
        ),
        (
            "{by: main, ask: Go?, when: x}",
            "$.root.seq[0].gate.when",
            "when is not a gate field",
        ),
        (
            '{by: main, ask: "{{later.x}}"}',
            "$.root.seq[0].gate.ask",
            "later may not have finished",
        ),
    ],
)
def test_a_bad_gate_is_refused_at_its_path(gate, path, message):
    text = (
        "id: g\ntitle: G\nsay: G.\nroot:\n  seq:\n"
        f"    - {{gate: {gate}, id: ok, say: Ask.}}\n"
        '    - {sh: "true", id: later, say: L.}\n  say: S.\n'
    )
    _, errors = errs(text)
    assert any(e.startswith(path) and message in e for e in errors), errors


def test_a_gate_takes_no_retries():
    _, errors = errs(
        "id: g\ntitle: G\nsay: G.\nroot: {gate: {by: main, ask: Go?}, retries: 1, say: Ask.}\n"
    )
    assert (
        "$.root.retries: retries go on agent, sh and check; repeat a group with loop"
        in errors
    )


def test_a_gate_shows_only_what_has_finished():
    text = (
        "id: g\ntitle: G\nsay: G.\nroot:\n  seq:\n"
        "    - {gate: {by: main, ask: Go?, show: [later]}, id: ok, say: Ask.}\n"
        '    - {sh: "true", id: later, say: L.}\n  say: S.\n'
    )
    _, errors = errs(text)
    assert any(
        e.startswith("$.root.seq[0].gate.show") and "later" in e for e in errors
    ), errors


REUSE = """id: r
title: R
say: Write, then fix it in the same session.
root:
  seq:
    - {{id: impl, say: Write it., agent: {{agent: opus, prompt: write}}}}
    - {{id: fix, say: Fix it., agent: {{{fix}}}}}
  say: Both.
"""


def test_a_step_runs_in_an_earlier_steps_session():
    wf, errors = errs(REUSE.format(fix="session: impl, prompt: fix"))
    assert errors == []
    assert wf.session_of == {"fix": "impl"} and wf.users == {"impl": ["fix"]}
    assert [r.get("session") for r in rows(wf)] == [None, None, "impl"]


def test_what_a_reused_session_refuses():
    for fix, msg in (
        (
            "session: impl, agent: opus, prompt: fix",
            "agent does not go with session: impl: the step runs in impl's session",
        ),
        ("session: nobody, prompt: fix", "session: nobody names no earlier agent step"),
        ("session: fix, prompt: fix", "session: fix names no earlier agent step"),
    ):
        _, got = errs(REUSE.format(fix=fix))
        assert any(msg in e for e in got), (fix, got)


def test_a_session_used_before_its_step_finishes_is_refused():
    text = """id: r
title: R
say: S.
root:
  par:
    - {id: impl, say: W., agent: {agent: opus, prompt: w}}
    - {id: fix, say: F., agent: {session: impl, prompt: f}}
  say: Both at once.
"""
    _, got = errs(text)
    assert any("impl may not have finished before this step starts" in e for e in got)


def test_two_users_of_one_session_must_not_overlap():
    text = """id: r
title: R
say: S.
root:
  seq:
    - {id: impl, say: W., agent: {agent: opus, prompt: w}}
    - par:
        - {id: a, say: A., agent: {session: impl, prompt: a}}
        - {id: b, say: B., agent: {session: impl, prompt: b}}
      say: Both at once.
  say: All.
"""
    _, got = errs(text)
    assert any("may run at the same time as" in e for e in got), got


def test_a_session_from_outside_a_map_is_refused_inside_it():
    text = """id: r
title: R
say: S.
root:
  seq:
    - {id: impl, say: W., agent: {agent: opus, prompt: w}}
    - map: {over: [1, 2], as: x, do: {id: f, say: F., agent: {session: impl, prompt: f}}}
      say: Each.
  say: All.
"""
    _, got = errs(text)
    assert any("every item of map" in e for e in got), got


def test_a_chain_resolves_to_the_first_session_and_a_loop_may_reuse():
    text = """id: r
title: R
say: S.
root:
  seq:
    - {id: impl, say: W., agent: {agent: opus, prompt: w}}
    - {id: review, say: R., agent: {session: impl, prompt: r}}
    - loop:
        max: 3
        do: {id: fix, say: F., agent: {session: review, prompt: f}}
      id: rounds
      say: Up to three fixes.
  say: All.
"""
    wf, errors = errs(text)
    assert errors == [] and wf.session_of["rounds/fix"] == "impl"
    assert wf.users["impl"] == ["review", "rounds/fix"]


def test_two_users_in_the_two_branches_of_an_if_never_overlap():
    text = """id: r
title: R
say: S.
root:
  seq:
    - {id: impl, say: W., agent: {agent: opus, prompt: w}}
    - if:
        when: {expr: "true"}
        then: {id: a, say: A., agent: {session: impl, prompt: a}}
        else: {id: b, say: B., agent: {session: impl, prompt: b}}
      say: One or the other.
  say: All.
"""
    wf, errors = errs(text)
    assert errors == [] and wf.users["impl"] == ["a", "b"], errors


LOOP = (
    "id: l\ntitle: L\nsay: L.\nroot:\n  loop:\n    max: 2\n{extra}"
    "    do: {{sh: 'true', say: Once.}}\n  id: lp\n  say: Loop.\n"
)


def test_at_max_is_done_or_fail_and_needs_until():
    until = "    until: {expr: 'false'}\n"
    wf, got = errs(LOOP.format(extra=until + "    at_max: done\n"))
    assert got == [] and wf.root.body["at_max"] == "done"
    wf, got = errs(LOOP.format(extra=until + "    at_max: fail\n"))
    assert got == [] and "at_max" not in wf.root.body  # fail is the default
    assert errs(LOOP.format(extra=until + "    at_max: stop\n"))[1] == [
        "$.root.loop.at_max: at_max is done or fail: what the loop is when its "
        "rounds run out before until passes"
    ]
    assert errs(LOOP.format(extra="    at_max: done\n"))[1] == [
        "$.root.loop.at_max: at_max goes with until; a loop without until is "
        "done after its last round already"
    ]


def test_a_started_loop_keeps_its_at_max():
    until = "    until: {expr: 'false'}\n"
    old, _ = errs(LOOP.format(extra=until))
    new, _ = errs(LOOP.format(extra=until + "    at_max: done\n"))
    errors = edit_errors(old, new, {old.root.key: "begun"})
    assert [e.message for e in errors] == ["a started loop keeps its at_max"]


SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},
        "max": {"type": "integer", "default": 20},
        "opts": {"type": "object", "properties": {"fast": {"type": "boolean"}}},
    },
    "required": ["prompt"],
}


def test_defaults_fill_only_what_is_missing():
    assert with_defaults({"prompt": "p"}, SCHEMA) == {"prompt": "p", "max": 20}
    assert with_defaults({"prompt": "p", "max": 3}, SCHEMA)["max"] == 3
    assert with_defaults({}, {}) == {}


def test_a_closed_check_refuses_a_key_the_schema_does_not_name_at_any_depth():
    good = {"prompt": "p", "opts": {"fast": True}}
    assert check_value(good, SCHEMA, "$.args", closed=True) == []
    extra = {"prompt": "p", "shell": "rm", "opts": {"fast": True, "x": 1}}
    assert check_value(extra, SCHEMA, "$.args") == []  # open, as results are
    # An object's properties are checked first, nested ones included, then
    # the keys it should not have.
    assert check_value(extra, SCHEMA, "$.args", closed=True) == [
        "$.args.opts.x: not in the schema",
        "$.args.shell: not in the schema",
    ]
