"""What a live edit may change, judged against what has run."""

import pytest

from aegis.workflow_lang import edit_diff, edit_errors, parse

AGENTS = {"opus", "sonnet"}

OLD = """id: rel
title: Release
say: Release.
root:
  seq:
    - {agent: {agent: opus, prompt: "Build."}, id: build, say: Build it.}
    - map: {over: [a, b], as: s, do: {sh: "echo {{s}}", id: one, say: One.}}
      id: each
      say: Each.
    - {sh: "true", id: last, say: Last.}
  say: All.
"""


def wf(text):
    w, errors = parse(text, AGENTS)
    assert errors == [], errors
    return w


def check(new: str, seen: dict) -> list[str]:
    return [f"{e.path}: {e.message}" for e in edit_errors(wf(OLD), wf(new), seen)]


def test_a_new_step_after_what_ran_is_fine_and_reads_as_an_add():
    new = OLD.replace(
        '    - {sh: "true", id: last, say: Last.}',
        '    - {sh: "true", id: last, say: Last.}\n    - {sh: "make notes", id: notes, say: Write notes.}',
    )
    assert check(new, {"build": "ran", "n1": "begun"}) == []
    d = edit_diff(wf(OLD), wf(new))
    assert [(x["op"], x["say"]) for x in d["diff"]] == [("add", "Write notes.")]
    assert d["touched"] == ["notes"] and d["commands"] == {"sh": 1, "probes": 0}


@pytest.mark.parametrize(
    "seen, edit, expect",
    [
        (
            {"build": "running"},
            ('prompt: "Build."', 'prompt: "Build it well."'),
            "is running: stop it with workflow_stop",
        ),
        (
            {"build": "ran"},
            ('prompt: "Build."', 'prompt: "Build it well."'),
            "has run and keeps its result",
        ),
        (
            {"build": "ran"},
            (
                '    - {agent: {agent: opus, prompt: "Build."}, id: build, say: Build it.}\n',
                "",
            ),
            "has started, so it stays",
        ),
        (
            {"each": "begun"},
            ("over: [a, b]", "over: [a, b, c]"),
            "a started map keeps its over",
        ),
        (
            {"build": "ran", "n1": "begun"},
            ("  seq:\n", '  seq:\n    - {sh: "true", id: first, say: First.}\n'),
            "a new step cannot go before build, which has started",
        ),
        (
            {"each": "finished", "each/one": "ran"},
            (
                'do: {sh: "echo {{s}}", id: one, say: One.}',
                'do: {seq: [{sh: "echo {{s}}", id: one, say: One.}, {sh: "true", id: two, say: Two.}], say: Do.}',
            ),
            "each has finished",
        ),
    ],
)
def test_what_an_edit_may_not_change(seen, edit, expect):
    errors = check(OLD.replace(*edit), seen)
    assert any(expect in e for e in errors), errors


def test_a_failed_step_may_change():
    assert (
        check(
            OLD.replace('prompt: "Build."', 'prompt: "Build, then test."'),
            {"build": "parked"},
        )
        == []
    )


def test_the_id_and_args_stay():
    errors = check(OLD.replace("id: rel", "id: other"), {})
    assert "$.id: a live edit keeps the workflow's id, rel" in errors


def test_a_generated_id_that_moved_is_named():
    old = OLD.replace(", id: build", "")  # build is now n2
    new = old.replace("  seq:\n", '  seq:\n    - {sh: "true", say: New.}\n')
    errors = [
        f"{e.path}: {e.message}"
        for e in edit_errors(wf(old), wf(new), {"n2": "ran", "n1": "begun"})
    ]
    assert any("generated id n2" in e for e in errors), errors


def test_the_diff_names_a_change_a_removal_and_the_header():
    new = (
        OLD.replace('prompt: "Build."', 'prompt: "Build it well."')
        .replace("say: Build it.", "say: Build it well.")
        .replace('    - {sh: "true", id: last, say: Last.}\n', "")
        .replace("title: Release", "title: Ship")
        .replace('do: {sh: "echo {{s}}"', 'do: {sh: "echo {{s}} >> out"')
    )
    d = edit_diff(wf(OLD), wf(new))
    assert [(x["op"], x["key"], x.get("fields")) for x in d["diff"]] == [
        ("change", "$", ["its title"]),
        ("change", "build", ["its prompt"]),
        ("change", "each/one", ["its command"]),
        ("remove", "last", None),
    ]
    build, one, last = d["diff"][1:]
    assert build["was"] == "Build it." and build["say"] == "Build it well."
    assert one["cmd"] == "echo {{s}} >> out" and one["depth"] == 2
    assert last["depth"] == 1 and "cmd" not in last
    assert d["touched"] == ["build", "each/one", "last"]
    assert d["commands"] == {"sh": 1, "probes": 0}


ABC = """id: r
title: R
say: R.
root:
  seq:
    - {sh: "sleep 100", id: a, say: A.}
    - {sh: "rm -rf build", id: b, say: B.}
    - {sh: "true", id: c, say: C.}
  say: All.
"""


def test_a_begun_seq_keeps_the_order_of_its_steps():
    # b would start beside the running a, and the diff showed only c's change.
    new = ABC.replace(
        '    - {sh: "sleep 100", id: a, say: A.}\n    - {sh: "rm -rf build", id: b, say: B.}\n',
        '    - {sh: "rm -rf build", id: b, say: B.}\n    - {sh: "sleep 100", id: a, say: A.}\n',
    ).replace("say: C.", "say: C two.")
    old = wf(ABC)
    errors = edit_errors(old, wf(new), {"a": "running", old.root.key: "begun"})
    assert any("keeps the order of its steps" in e.message for e in errors), errors
    # Not begun, its order is the author's to change.
    assert edit_errors(old, wf(new), {}) == []


def test_a_step_moved_to_another_group_is_its_own_diff_line():
    old = ABC.replace(
        '    - {sh: "true", id: c, say: C.}\n',
        '    - {seq: [{sh: "true", id: x, say: X.}], id: grp, say: Grp.}\n'
        '    - {sh: "true", id: c, say: C.}\n',
    )
    new = old.replace(
        '[{sh: "true", id: x, say: X.}]',
        '[{sh: "true", id: x, say: X.}, {sh: "rm -rf build", id: b, say: B.}]',
    ).replace('    - {sh: "rm -rf build", id: b, say: B.}\n', "")
    d = edit_diff(wf(old), wf(new))
    assert [(x["op"], x["key"]) for x in d["diff"]] == [("move", "b")]
    assert d["diff"][0]["fields"] == ["its place"] and "b" in d["touched"]
    assert d["diff"][0]["cmd"] == "rm -rf build"


def test_a_generated_id_with_state_never_changes_its_step():
    # A step inserted first renumbers the rest: n2 would turn from the
    # failed Deploy into Pay, which would inherit Deploy's failure and run.
    old = """id: r
title: R
say: R.
root:
  par:
    - {sh: "false", say: Deploy.}
    - {sh: "true", say: Tidy.}
  say: All.
"""
    new = old.replace("  par:\n", '  par:\n    - {sh: "curl -X POST pay", say: Pay.}\n')
    o, n = wf(old), wf(new)
    assert o.root.children[0].key == n.root.children[0].key == "n2"
    for how in ("parked", "running", "ran"):
        errors = [e.message for e in edit_errors(o, n, {"n2": how})]
        assert any("generated id n2" in e for e in errors), (how, errors)
    # With the id written on it, the failed step may change as before.
    pinned = old.replace("say: Deploy.}", "id: n2, say: Deploy.}")
    fixed = pinned.replace('"false", id: n2', '"exit 0", id: n2')
    assert edit_errors(wf(pinned), wf(fixed), {"n2": "parked"}) == []


def test_the_diff_shows_probe_commands_and_counts_only_new_ones():
    old = ABC.replace(
        '    - {sh: "true", id: c, say: C.}\n',
        '    - {check: {sh: "test -f ok", every: 5s}, id: c, say: C.}\n'
        '    - {if: {when: {sh: "test -f x"}, then: {wait: 1s, id: w, say: W.}}, id: i, say: I.}\n',
    )
    new = (
        old.replace('"test -f ok"', '"curl -X POST ok"')
        .replace("id: i, say: I.", "id: i, say: I renamed.")
        .replace("say: B.", "say: B renamed.")
    )
    d = edit_diff(wf(old), wf(new))
    by = {x["key"]: x for x in d["diff"]}
    assert by["c"]["cmd"] == "curl -X POST ok" and by["i"]["cmd"] == "test -f x"
    assert by["b"]["cmd"] == "rm -rf build"
    # Only the check's probe changed its command; b and i changed their say.
    assert d["commands"] == {"sh": 0, "probes": 1}
