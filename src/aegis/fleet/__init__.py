"""The fleet dashboard: one card per live session, no transcript.

Assembly (`snapshot`) reads the live SessionManager; rendering (`render`)
is pure functions over the dataclasses in `models`, so the grid is tested
without a Textual app. Same split as `aegis.tui.sidebar`.
"""

from aegis.fleet.models import Origin

__all__ = ["Origin"]
