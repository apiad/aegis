"""Provider-object Agent shape — the preferred construction form for
multi-provider use. The flat ``Agent(harness=...)`` shape is covered by
the existing test_config.py + test_driver_argv.py."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from aegis import (
    Agent, ClaudeCode, Effort, GeminiCLI, OpenCode, Permission,
)


def test_claude_code_provider_round_trip():
    p = ClaudeCode(model="opus", effort=Effort.high,
                   permission=Permission.auto)
    assert p.name == "claude-code"
    a = Agent(provider=p)
    assert a.provider is p
    # Legacy flat fields derived from provider — drivers read these.
    assert a.harness == "claude-code"
    assert a.model == "opus"
    assert a.effort is Effort.high
    assert a.permission is Permission.auto


def test_gemini_provider_round_trip():
    p = GeminiCLI(model="gemini-3-flash-preview",
                  permission=Permission.full)
    assert p.name == "gemini"
    # GeminiCLI does NOT have an effort field — accessing it on the
    # Agent falls through to the default (high) but agent.provider has
    # no effort attribute.
    a = Agent(provider=p)
    assert a.harness == "gemini"
    assert a.model == "gemini-3-flash-preview"
    assert a.permission is Permission.full
    assert not hasattr(p, "effort")


def test_opencode_provider_round_trip():
    p = OpenCode(model="opencode/claude-sonnet-4-6",
                 permission=Permission.full)
    assert p.name == "opencode"
    a = Agent(provider=p)
    assert a.harness == "opencode"
    assert a.model == "opencode/claude-sonnet-4-6"
    assert a.permission is Permission.full


def test_flat_shape_still_works_and_synthesizes_provider():
    """Legacy ``Agent(harness=, model=, effort=, permission=)`` keeps
    working; a Provider object is synthesized so internal code can
    consume agent.provider uniformly."""
    a = Agent(harness="claude-code", model="sonnet",
              effort=Effort.medium, permission=Permission.auto)
    assert isinstance(a.provider, ClaudeCode)
    assert a.provider.model == "sonnet"
    assert a.provider.effort is Effort.medium


def test_flat_shape_for_gemini_synthesizes_geminicli():
    a = Agent(harness="gemini", model="gemini-3-flash-preview",
              effort=Effort.high, permission=Permission.full)
    assert isinstance(a.provider, GeminiCLI)
    assert a.provider.model == "gemini-3-flash-preview"


def test_flat_shape_for_opencode_synthesizes_opencode():
    a = Agent(harness="opencode", model="opencode/gemini-3-flash",
              effort=Effort.high, permission=Permission.full)
    assert isinstance(a.provider, OpenCode)
    assert a.provider.model == "opencode/gemini-3-flash"


def test_flat_shape_unknown_harness_raises():
    with pytest.raises(ValidationError, match="unknown harness"):
        Agent(harness="ghost", model="x", effort=Effort.high,
              permission=Permission.auto)


def test_neither_shape_raises():
    with pytest.raises(ValidationError, match="either provider="):
        Agent(model="x", effort=Effort.high, permission=Permission.auto)


def test_provider_defaults_for_gemini_are_full_permission():
    """Gemini headless without --yolo prompts interactively; full
    (yolo) is the sensible default for queue workers / workflow drive."""
    p = GeminiCLI(model="gemini-3-flash-preview")
    assert p.permission is Permission.full


def test_provider_defaults_for_opencode_are_full_permission():
    p = OpenCode(model="opencode/claude-sonnet-4-6")
    assert p.permission is Permission.full
