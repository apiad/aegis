"""Which git repos the live agents are writing to.

aegis runs many agents over one checkout, and nothing on screen says where
they are standing. This package answers that: a tracker that learns repo
membership from the write tools an agent calls, a probe that reads each
repo's git state, and a pure renderer for the ``REPOS`` section of the F3
sidebar.

Design: ``docs/superpowers/specs/2026-08-10-aegis-sidebar-repos-section-design.md``
"""
from aegis.repos.models import RepoState, RepoView

__all__ = ["RepoState", "RepoView"]
