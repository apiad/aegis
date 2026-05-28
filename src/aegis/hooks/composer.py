"""Compose multiple PreTurnResults from a single pre_turn fire."""
from __future__ import annotations

from aegis.hooks.contexts import PreTurnResult


class ComposerError(Exception):
    """Two or more hooks returned conflicting mutations this turn."""


def compose_pre_turn(results: list[PreTurnResult]) -> PreTurnResult:
    """Apply the composition rules from the design spec.

    Rules:
    - prepend_system strings concatenate in declaration order, separated by "\\n\\n".
    - rewrite_user: at most one non-None across all results, else fail-loud.
    - block: first non-None wins; later block values are ignored (but their
      sibling fields are still recorded so users can introspect).
    - extend_history: tuples concatenate in declaration order.
    """
    prepends:  list[str] = []
    rewrite:   str | None = None
    block:     str | None = None
    history:   list = []

    for r in results:
        if r.prepend_system is not None:
            prepends.append(r.prepend_system)
        if r.rewrite_user is not None:
            if rewrite is not None:
                raise ComposerError(
                    "two hooks returned rewrite_user; only one allowed"
                )
            rewrite = r.rewrite_user
        if r.block is not None and block is None:
            block = r.block
        if r.extend_history:
            history.extend(r.extend_history)

    return PreTurnResult(
        prepend_system="\n\n".join(prepends) if prepends else None,
        rewrite_user=rewrite,
        block=block,
        extend_history=tuple(history) if history else None,
    )
