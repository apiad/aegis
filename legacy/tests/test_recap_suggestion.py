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
    """Each assertion is a probe finding that cost money to learn.

    Measured over 40 real turn boundaries
    (`.playground/reply-suggestion-probe/`): the trigger has to name the
    waiting-for-a-go-ahead case, because a rule that says "a question was
    asked" made the model invent answers to open questions; and the
    capitalisation and full-stop bans are the measured habit of the
    operator's own messages, 0 of 40 of which break either.
    """
    assert "waiting for a go-ahead" in SYSTEM
    assert "capital letter" in SYSTEM
    assert "end it with a period" in SYSTEM
    assert "Ten words at most" in SYSTEM
    # Empty is the default answer; a wrong suggestion costs more than none.
    assert "LEAVE IT EMPTY" in SYSTEM
