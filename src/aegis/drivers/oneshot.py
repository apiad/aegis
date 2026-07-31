"""Shared plumbing for the one-shot ``generate()`` seam.

Two drivers can be handed a JSON schema natively (claude, lovelaice); two
cannot (gemini, opencode) and have to be asked politely and parsed
tolerantly. ``parse_structured`` is that tolerant parse, shared rather than
written four times.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_DECODER = json.JSONDecoder()


@dataclass(frozen=True)
class Generation:
    """One one-shot call: what came back, and what it cost.

    ``value`` is None whenever the call failed or the model's payload did
    not validate — callers treat generation as best-effort. The telemetry
    rides along because ``/btw`` renders it: a side note is a paid call and
    the price should be visible.
    """
    value: BaseModel | None = None
    model: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0


def _candidates(raw: str):
    """Substrings that might be the JSON object, best bet first."""
    for block in _FENCE.findall(raw):
        yield block
    yield raw
    # A bare object embedded in prose. raw_decode from each '{' finds the
    # outermost one first, which is the one the model meant.
    for match in re.finditer(r"\{", raw):
        try:
            obj, _ = _DECODER.raw_decode(raw, match.start())
        except ValueError:
            continue
        yield json.dumps(obj)


def parse_structured(raw: str, schema: type[T]) -> T | None:
    """The model's text as a validated ``schema`` instance, or None.

    Accepts a bare object, a fenced block, or an object embedded in prose.
    Never raises: a malformed payload is a missing answer, not an exception
    that reaches the conversation.
    """
    if not raw or not raw.strip():
        return None
    for candidate in _candidates(raw):
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            return schema.model_validate(data)
        except ValidationError:
            continue
    return None
