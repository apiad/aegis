"""The AFK coordinator: a board of prompt cards, emptied while you sleep.

See docs/superpowers/specs/2026-09-25-afk-coordinator-design.md. The
workflows are registered here; everything they call lives in the sibling
modules, which hold no aegis state and are testable on their own.
"""
