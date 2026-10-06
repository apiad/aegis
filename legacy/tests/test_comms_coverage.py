"""Every tool the MCP server registers must have a descriptor.

This is the test that stops tool seventy-three from silently falling out of
the format. A tool with no descriptor renders as the generic dot with the
first-stringy-argument fallback — which is exactly the state this whole
feature exists to end, and nothing else would notice.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from aegis.comms.descriptors import (ADMIN, CONVERSATION, COORDINATION,
                                     INTROSPECTION, descriptor_for)
from aegis.mcp.server import build_server

_FAMILIES = {CONVERSATION, COORDINATION, INTROSPECTION, ADMIN}


@pytest.fixture(scope="module")
def tool_names() -> list[str]:
    server = build_server(MagicMock())
    return sorted(t.name for t in asyncio.run(server.list_tools()))


def test_every_registered_tool_has_a_descriptor(tool_names):
    missing = [n for n in tool_names if descriptor_for(n) is None]
    assert missing == [], (
        f"{len(missing)} aegis tools have no comms descriptor: {missing}")


def test_every_descriptor_declares_a_known_family(tool_names):
    for name in tool_names:
        d = descriptor_for(name)
        assert d.family in _FAMILIES, f"{name} has family {d.family!r}"


def test_every_descriptor_survives_empty_arguments(tool_names):
    """The model can call any tool with anything. A descriptor that raises
    on a missing argument would take down the transcript paint."""
    for name in tool_names:
        d = descriptor_for(name)
        glyph = d.glyph({}) if callable(d.glyph) else d.glyph
        assert isinstance(glyph, str) and glyph
        assert isinstance(d.describe({}), str)
        assert d.target({}) is None or d.target({}).kind
