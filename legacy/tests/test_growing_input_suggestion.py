"""The drafted reply is the widget's placeholder — dim, and gone on a keystroke.

Nothing is ever inserted into the document until Tab, which is why history
recall, the palette's key interceptor and the voice lock need to know nothing
about this feature.
"""

import pytest
from textual import events

from aegis.tui.widgets import GrowingInput


def make() -> GrowingInput:
    return GrowingInput(placeholder=GrowingInput.PLACEHOLDER)


def test_setting_a_suggestion_becomes_the_placeholder():
    w = make()
    w.suggestion = "one call, fifth field"
    assert w.placeholder == "one call, fifth field"


def test_clearing_restores_the_default_placeholder():
    w = make()
    w.suggestion = "one call"
    w.suggestion = ""
    assert w.suggestion == ""
    assert w.placeholder == GrowingInput.PLACEHOLDER


def test_a_suggestion_is_refused_while_the_box_has_text():
    """The placeholder is invisible behind text, so accepting one on Tab
    would replace what the operator was halfway through typing."""
    w = make()
    w.value = "half a sentence"
    w.suggestion = "one call"
    assert w.suggestion == ""
    assert w.placeholder == GrowingInput.PLACEHOLDER


def test_accept_fills_the_box_and_consumes_the_suggestion():
    w = make()
    w.suggestion = "one call, fifth field"
    assert w.accept_suggestion() is True
    assert w.value == "one call, fifth field"
    assert w.suggestion == ""
    assert w.placeholder == GrowingInput.PLACEHOLDER


def test_accept_is_a_no_op_with_nothing_to_accept():
    w = make()
    assert w.accept_suggestion() is False
    assert w.value == ""


def test_submitting_clears_a_stale_suggestion():
    w = make()
    w.suggestion = "one call"
    w._record_history("something else")
    assert w.suggestion == ""


@pytest.mark.asyncio
async def test_tab_accepts_the_suggestion():
    w = make()
    w.suggestion = "one call"
    ev = events.Key("tab", None)
    await w._on_key(ev)
    assert w.value == "one call"
    assert ev._stop_propagation is True


@pytest.mark.asyncio
async def test_tab_with_no_suggestion_is_left_alone():
    """Without one, Tab stays what it has always been: focus-next."""
    w = make()
    ev = events.Key("tab", None)
    await w._on_key(ev)
    assert w.value == ""
    assert ev._stop_propagation is False
