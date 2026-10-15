"""The one evaluator behind every {{...}} and every expr probe: what it reads,
what it refuses, and that nothing in it runs code."""

import pytest

from aegis.workflow_lang import (
    ExprError,
    compile_expr,
    evaluate,
    refs,
    render,
    value_of,
)

SCOPE = {
    "args": {"issue": 197, "every": None, "title": "a; touch pwned"},
    "tick": {"done": False, "next": "5m", "n": 3},
    "m": {"0": {"one": {"pr": 12}}},
    "xs": {"list": [10, 20]},
    "": {},
}


@pytest.mark.parametrize(
    "text,value",
    [
        ("tick.done", False),
        ("not tick.done", True),
        ("tick.n == 3", True),
        ("tick.n != 3", False),
        ("tick.n >= 3 and tick.n < 4", True),
        ("tick.next == '5m'", True),
        ('"b" > "a"', True),
        ("args.every or tick.next or '20m'", "5m"),
        ("args.missing or '20m'", "20m"),
        ("tick.done and nothing.here", False),
        ("(tick.done or true) and not false", True),
        ("null", None),
        ("-1.5 < 0", True),
        ("m.0.one.pr", 12),
        ("xs.list.1", 20),
        ("true == 1", False),
        ("1 == 1.0", True),
        ("'it\\'s'", "it's"),
    ],
)
def test_an_expression_evaluates(text, value):
    assert evaluate(compile_expr(text), SCOPE) == value


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",
        "a.b()",
        "1 + 1",
        "a < b < c",
        "a[0]",
        "",
        "tick.done and",
        "(tick.done",
        "'unclosed",
        "lambda: 1",
    ],
)
def test_what_is_not_the_grammar_is_refused(text):
    with pytest.raises(ExprError):
        compile_expr(text)


def test_ordering_two_kinds_is_an_error_not_an_answer():
    with pytest.raises(ExprError, match="two numbers or two strings"):
        evaluate(compile_expr("tick.next < 3"), SCOPE)


def test_refs_lists_every_reference_once_in_order():
    tree = compile_expr("impl.pr == review.pr or not args.skip")
    assert [(r.name, r.parts) for r in refs(tree)] == [
        ("impl", ("pr",)),
        ("review", ("pr",)),
        ("args", ("skip",)),
    ]


def test_render_writes_strings_as_they_are_and_the_rest_as_json():
    assert render("PR {{m.0.one.pr}} done={{tick.done}}", SCOPE) == "PR 12 done=false"
    assert render("{{args.every or 'x'}}", SCOPE) == "x"


def test_a_whole_template_that_comes_to_null_has_no_value():
    with pytest.raises(KeyError, match="has no value"):
        render("wait {{args.every}}", SCOPE)


def test_value_of_reads_one_whole_expression_raw():
    assert value_of("{{xs.list}}", SCOPE) == [10, 20]
    assert value_of("{{args.every or 20}}", SCOPE) == 20
    assert value_of("not a template", SCOPE) == "not a template"
