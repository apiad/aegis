"""Shared canvas — blackboard coordination primitive.

A canvas is a markdown file partitioned into sections by ``##`` headings.
Multiple agents read, write sections, and subscribe to change events
delivered via the existing inbox substrate.

Spec: docs/superpowers/specs/2026-05-21-shared-canvas-design.md
"""
from aegis.canvas.parser import (
    BODY,
    PREAMBLE,
    InvalidSection,
    Section,
    parse_sections,
    render_sections,
    section_names,
    valid_section_name,
)

__all__ = [
    "BODY",
    "InvalidSection",
    "PREAMBLE",
    "Section",
    "parse_sections",
    "render_sections",
    "section_names",
    "valid_section_name",
]
