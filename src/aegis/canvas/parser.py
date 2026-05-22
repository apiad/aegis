"""Pure markdown section parser/writer for shared canvases.

Sections are top-level ``## headings``. Pre-``##`` text goes in the
implicit ``_preamble`` section. A file with no ``##`` headings is one
big ``body`` section.

No I/O here — this module just round-trips text ↔ ordered sections.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PREAMBLE = "_preamble"
BODY = "body"

# Allowed section name characters: alphanumeric, dash, underscore, space.
# Implicit names (PREAMBLE, BODY) start with the otherwise-disallowed
# ``_`` / are reserved; agents pass these by name like any other.
_NAME_RE = re.compile(r"^[A-Za-z0-9_\- ]+$")

# ``## heading`` line. Captures the heading text (trimmed).
_HEADING_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$")


class InvalidSection(ValueError):
    """Raised when a section name violates the naming rules."""


@dataclass(frozen=True)
class Section:
    name: str
    body: str  # body without the heading line; may be empty


def valid_section_name(name: str) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if name in (PREAMBLE, BODY):
        return True
    if name.startswith(" ") or name.endswith(" "):
        return False
    return bool(_NAME_RE.match(name))


def _require_valid(name: str) -> None:
    if not valid_section_name(name):
        raise InvalidSection(
            f"invalid section name: {name!r} "
            f"(allowed: alphanumeric, dash, underscore, space)")


def parse_sections(text: str) -> list[Section]:
    """Split markdown text into ordered Sections.

    - Lines before the first ``##`` form a ``_preamble`` section (only if
      non-empty).
    - If there are no ``##`` headings at all, the whole text is one
      ``body`` section (only if non-empty).
    - Each ``## name`` starts a new section whose body is the lines up
      to (but not including) the next ``## name`` or EOF.
    """
    if text == "":
        return []
    lines = text.splitlines()
    # Find heading positions.
    heads: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            heads.append((i, m.group(1)))
    if not heads:
        return [Section(name=BODY, body=text)]
    out: list[Section] = []
    # Preamble = lines [0 .. heads[0][0])
    pre_lines = lines[: heads[0][0]]
    preamble_body = "\n".join(pre_lines)
    # Preserve trailing newline if the text had one before the first ##.
    if pre_lines:
        # If the original text had a newline at the very end of preamble
        # region (i.e., the line before the heading was empty or content+\n),
        # join with \n. Add trailing \n if there were lines, to keep the
        # block-style separation when rendering back.
        preamble_body = "\n".join(pre_lines)
        if preamble_body.strip() != "" or any(l != "" for l in pre_lines):
            out.append(Section(name=PREAMBLE, body=preamble_body))
    # Each heading -> next heading or EOF
    for idx, (line_no, name) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        body_lines = lines[line_no + 1: end]
        body = "\n".join(body_lines)
        out.append(Section(name=name, body=body))
    return out


def render_sections(sections: list[Section]) -> str:
    """Render Sections back to a single markdown string."""
    parts: list[str] = []
    for sec in sections:
        if sec.name == PREAMBLE:
            parts.append(sec.body)
        elif sec.name == BODY:
            parts.append(sec.body)
        else:
            # Heading + blank line separator already implied; render
            # body verbatim.
            parts.append(f"## {sec.name}\n{sec.body}")
    # Join with single newline; rely on caller to manage trailing newline.
    text = "\n".join(parts)
    return text


def section_names(sections: list[Section]) -> list[str]:
    return [s.name for s in sections]


def find_section(sections: list[Section], name: str) -> Section | None:
    for s in sections:
        if s.name == name:
            return s
    return None


def write_section(sections: list[Section], name: str,
                  content: str) -> list[Section]:
    """Return a new section list with ``name`` set to ``content``.

    - Existing section: body replaced.
    - Missing section: appended at the end.
    - Implicit names (PREAMBLE, BODY) follow the same shape — PREAMBLE
      is moved to the front if it is being created; BODY only valid in
      a file that has no ``##`` headings (caller's responsibility to
      check before passing).
    """
    _require_valid(name)
    new = [Section(name=s.name, body=s.body) for s in sections]
    for i, s in enumerate(new):
        if s.name == name:
            new[i] = Section(name=name, body=content)
            return new
    # Missing — append (preamble goes to front instead).
    if name == PREAMBLE:
        new.insert(0, Section(name=PREAMBLE, body=content))
    else:
        new.append(Section(name=name, body=content))
    return new


def append_to_section(sections: list[Section], name: str,
                      text: str) -> list[Section]:
    """Return a new section list with ``text`` appended to ``name``.

    Joined with a single newline if existing body is non-empty.
    Missing section is created with just ``text`` as body.
    """
    _require_valid(name)
    existing = find_section(sections, name)
    if existing is None:
        return write_section(sections, name, text)
    new_body = (existing.body + "\n" + text) if existing.body else text
    return write_section(sections, name, new_body)


def section_line_count(body: str) -> int:
    """Line count for diff math. Empty body = 0; trailing newline ignored."""
    if body == "":
        return 0
    return len(body.splitlines())
