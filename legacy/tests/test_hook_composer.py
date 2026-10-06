"""PreTurnResult composition: block short-circuit, rewrite conflicts,
prepend concatenation, history extension."""
from __future__ import annotations

import pytest

from aegis.hooks.composer import ComposerError, compose_pre_turn
from aegis.hooks.contexts import PreTurnResult, Turn


def test_empty_composes_to_no_op() -> None:
    composed = compose_pre_turn([])
    assert composed == PreTurnResult()


def test_single_result_passes_through() -> None:
    r = PreTurnResult(prepend_system="hello")
    composed = compose_pre_turn([r])
    assert composed.prepend_system == "hello"


def test_prepend_system_concatenates_in_order() -> None:
    results = [
        PreTurnResult(prepend_system="A"),
        PreTurnResult(prepend_system="B"),
        PreTurnResult(prepend_system="C"),
    ]
    composed = compose_pre_turn(results)
    assert composed.prepend_system == "A\n\nB\n\nC"


def test_prepend_system_skips_none() -> None:
    results = [
        PreTurnResult(prepend_system="A"),
        PreTurnResult(),
        PreTurnResult(prepend_system="C"),
    ]
    assert compose_pre_turn(results).prepend_system == "A\n\nC"


def test_block_short_circuits() -> None:
    results = [
        PreTurnResult(prepend_system="ignored after block"),
        PreTurnResult(block="reason"),
    ]
    composed = compose_pre_turn(results)
    assert composed.block == "reason"
    assert composed.prepend_system == "ignored after block"


def test_first_block_wins() -> None:
    results = [
        PreTurnResult(block="first"),
        PreTurnResult(block="second"),
    ]
    assert compose_pre_turn(results).block == "first"


def test_rewrite_user_conflict_fails_loud() -> None:
    results = [
        PreTurnResult(rewrite_user="a"),
        PreTurnResult(rewrite_user="b"),
    ]
    with pytest.raises(ComposerError, match="rewrite_user"):
        compose_pre_turn(results)


def test_rewrite_user_single_passes() -> None:
    results = [PreTurnResult(rewrite_user="new")]
    assert compose_pre_turn(results).rewrite_user == "new"


def test_extend_history_concatenates_in_order() -> None:
    a = (Turn(role="user", content="x"),)
    b = (Turn(role="assistant", content="y"),)
    composed = compose_pre_turn([
        PreTurnResult(extend_history=a),
        PreTurnResult(extend_history=b),
    ])
    assert composed.extend_history == (
        Turn(role="user", content="x"),
        Turn(role="assistant", content="y"),
    )
