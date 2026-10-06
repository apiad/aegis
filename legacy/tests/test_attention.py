"""The attention category of a turn: the model proposes, hard signals decide."""

import pytest

from aegis.attention import CATEGORIES, LABELS, is_pending, mark, resolve, style_for
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)


def test_the_five_categories_in_urgency_order():
    assert CATEGORIES == ("needs_input", "error", "review", "waiting", "done")
    assert set(LABELS) == set(CATEGORIES)
    assert LABELS["needs_input"] == "needs you"


def test_the_model_decides_when_no_hard_signal_fires():
    for c in CATEGORIES:
        assert resolve(c, errored=False, ephemeral=False, waiting=False) == c


def test_an_error_result_overrides_the_model():
    assert resolve("done", errored=True, ephemeral=False, waiting=False) == "error"
    assert resolve("needs_input", errored=True, ephemeral=False, waiting=True) == "error"


def test_an_ephemeral_worker_never_needs_the_operator():
    assert resolve("needs_input", errored=False, ephemeral=True, waiting=False) == "done"


def test_a_live_wait_turns_calm_categories_into_waiting():
    assert resolve("done", errored=False, ephemeral=False, waiting=True) == "waiting"
    assert resolve("review", errored=False, ephemeral=False, waiting=True) == "waiting"
    assert resolve("needs_input", errored=False, ephemeral=False, waiting=True) == "needs_input"


@pytest.mark.parametrize("model", [None, "", "banana"])
def test_no_usable_model_answer_falls_back_to_the_hard_signals(model):
    assert resolve(model, errored=False, ephemeral=False, waiting=False) == "done"
    assert resolve(model, errored=False, ephemeral=False, waiting=True) == "waiting"


def test_pending_until_acked_except_waiting():
    assert is_pending("needs_input", seq=3, acked=2)
    assert not is_pending("needs_input", seq=3, acked=3)
    assert is_pending("waiting", seq=3, acked=3)
    assert not is_pending("", seq=0, acked=0)


def test_marks_use_the_current_palette_only():
    assert "?" in mark("needs_input", C) and C.accent in mark("needs_input", C)
    assert "✗" in mark("error", C) and C.error in mark("error", C)
    assert "◆" in mark("review", C)
    assert "⧗" in mark("waiting", C) and C.muted in mark("waiting", C)
    assert "✓" in mark("done", C) and C.ready in mark("done", C)


def test_a_blinking_mark_keeps_its_width():
    from rich.text import Text

    on = Text.from_markup(mark("needs_input", C))
    off = Text.from_markup(mark("needs_input", C, blink_off=True))
    assert on.cell_len == off.cell_len
    assert "?" not in off.plain


def test_style_for_each_category():
    assert style_for("needs_input", C) == C.accent
    assert style_for("error", C) == C.error
    assert style_for("done", C) == C.ready
