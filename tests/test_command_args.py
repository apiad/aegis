import pytest
from aegis.commands.args import Arg, Flag, ArgSpec, parse, ArgError


def test_required_positional_binds():
    args = parse(ArgSpec(positionals=(Arg("name"),)), "reviewers")
    assert args["name"] == "reviewers"


def test_missing_required_raises():
    with pytest.raises(ArgError):
        parse(ArgSpec(positionals=(Arg("name"),)), "")


def test_optional_positional_absent():
    spec = ArgSpec(positionals=(Arg("name"), Arg("agent", required=False)))
    args = parse(spec, "reviewers")
    assert args["name"] == "reviewers"
    assert args.get("agent") is None


def test_greedy_takes_raw_verbatim_remainder():
    spec = ArgSpec(positionals=(Arg("agent"),
                                Arg("prompt", required=False, greedy=True)))
    args = parse(spec, 'researcher write a poem "keep quotes"')
    assert args["agent"] == "researcher"
    assert args["prompt"] == 'write a poem "keep quotes"'


def test_quoting_on_nongreedy_token():
    args = parse(ArgSpec(positionals=(Arg("name"),)), '"two words"')
    assert args["name"] == "two words"


def test_leading_valued_flag_space_form():
    spec = ArgSpec(positionals=(Arg("agent"),), flags=(Flag("effort"),))
    args = parse(spec, "--effort high researcher")
    assert args.flags["effort"] == "high"
    assert args["agent"] == "researcher"


def test_leading_valued_flag_equals_form():
    spec = ArgSpec(positionals=(Arg("agent"),), flags=(Flag("effort"),))
    args = parse(spec, "--effort=high researcher")
    assert args.flags["effort"] == "high"


def test_boolean_flag_presence_and_default():
    spec = ArgSpec(positionals=(Arg("name"),),
                   flags=(Flag("ephemeral", takes_value=False),))
    assert parse(spec, "--ephemeral q1").flags["ephemeral"] is True
    assert parse(spec, "q1").flags["ephemeral"] is False


def test_unknown_flag_raises():
    with pytest.raises(ArgError):
        parse(ArgSpec(positionals=(Arg("name"),)), "--bogus q1")


def test_valued_flag_missing_value_raises():
    spec = ArgSpec(positionals=(Arg("agent"),), flags=(Flag("effort"),))
    with pytest.raises(ArgError):
        parse(spec, "--effort")


def test_excess_positional_without_greedy_raises():
    with pytest.raises(ArgError):
        parse(ArgSpec(positionals=(Arg("name"),)), "one two")
