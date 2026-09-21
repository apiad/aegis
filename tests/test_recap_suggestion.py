"""The recap's fifth field: a draft of the operator's own next message."""

from aegis.recap import SYSTEM, Recap, StandingRecap


def test_schema_carries_a_suggestion_field():
    r = StandingRecap(
        task="t",
        outcome="o",
        next="n",
        attention="needs_input",
        suggestion="yes, do it",
    )
    assert r.suggestion == "yes, do it"


def test_suggestion_defaults_to_empty():
    r = StandingRecap(task="t", outcome="o", next="", attention="done")
    assert r.suggestion == ""


def test_recap_dataclass_carries_the_suggestion():
    assert Recap().suggestion == ""
    assert Recap(suggestion="ship it").suggestion == "ship it"


def test_prompt_tells_the_model_to_write_as_the_operator():
    # The rule that makes the field a draft reply rather than a fourth summary.
    assert "first person" in SYSTEM
    assert "12 words" in SYSTEM
    # Empty is the default answer; a wrong suggestion costs more than none.
    assert "Leave it empty" in SYSTEM
