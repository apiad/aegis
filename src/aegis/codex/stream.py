"""Codex's app-server notifications, one stdout line at a time, as typed events.

Measured on codex-cli 0.162.1 (spec ``2026-10-09-aegis-2-codex-harness-design.md``;
fixtures in ``tests/fixtures/codex/``, recorded by ``scripts/record_codex.py``).
"""

from __future__ import annotations

LABEL = "Codex"
# Lines CodexProcess writes itself, for what only a response carries: the
# version (``aegis/initialize``), the thread (``aegis/thread``), the model a
# turn was sent with (``aegis/turn``) and a slash command's line
# (``aegis/command``).
OWN = "aegis/"
# What the store keeps. Everything else on the stream (status changes,
# warnings, MCP startup, rate limits, remote control) carries nothing a reload
# needs.
STORED = frozenset(
    {
        "turn/started",
        "turn/completed",
        "item/started",
        "item/completed",
        "thread/tokenUsage/updated",
        "thread/name/updated",
        "turn/plan/updated",
    }
)
# Folded live, never stored: the item's ``item/completed`` carries the text.
DELTAS = frozenset(
    {
        "item/agentMessage/delta",
        "item/reasoning/textDelta",
        "item/reasoning/summaryTextDelta",
    }
)
