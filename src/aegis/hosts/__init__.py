"""SSH execution hosts — running a harness process on another machine.

The local aegis keeps the session, the transcript, and the MCP peer
identity; only the harness subprocess runs elsewhere. See
``docs/superpowers/specs/2026-08-04-aegis-ssh-execution-hosts-design.md``.
"""
from __future__ import annotations
