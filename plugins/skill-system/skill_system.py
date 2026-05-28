"""skill-system plugin: replicates Claude-Code's skill-selection on any harness.

A pre_turn hook injects a numbered menu of available skills as a system
context block. A first-class MCP tool exposes load_skill(name) so the
agent can pull the full body when relevant.

Skills live as Claude-Code-compatible markdown files in
`<project>/.aegis/skills/*.md`:

    ---
    name: brainstorming
    description: Use before any creative work — explores intent first.
    ---

    (skill body in markdown)
"""
from __future__ import annotations

from pathlib import Path

import yaml

from aegis.hooks import hook, PreTurnContext, PreTurnResult
from aegis.tools import tool


SKILLS_SUBDIR = ".aegis/skills"


def _parse_skill(path: Path) -> tuple[str, str, str]:
    """Return (name, description, body). Skip files with no frontmatter."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"{path}: malformed frontmatter")
    front = text[4:end]
    body = text[end + len("\n---\n"):].lstrip()
    meta = yaml.safe_load(front) or {}
    name = meta.get("name") or path.stem
    desc = meta.get("description", "")
    return name, desc, body


def _load_index(project_root: Path) -> list[tuple[str, str]]:
    folder = project_root / SKILLS_SUBDIR
    if not folder.exists():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(folder.glob("*.md")):
        if path.name == "README.md":
            continue
        try:
            name, desc, _body = _parse_skill(path)
        except ValueError:
            continue
        out.append((name, desc))
    return out


@hook("pre_turn")
async def inject_menu(ctx: PreTurnContext) -> PreTurnResult | None:
    """Inject the skill menu as system context for this turn."""
    skills = _load_index(ctx.project_root)
    if not skills:
        return None
    lines = ["Available skills:\n"]
    for i, (name, desc) in enumerate(skills, 1):
        lines.append(f"{i}. {name} — {desc}")
    lines.append(
        "\nIf any are relevant to your task, call `load_skill(name)` "
        "to load the full body before proceeding."
    )
    return PreTurnResult(prepend_system="\n".join(lines))


@tool
async def load_skill(name: str) -> str:
    """Load the full body of a registered skill.

    Args:
        name: skill name as listed in the pre-turn menu.

    Returns:
        the skill's markdown body (the content after the YAML frontmatter).
    """
    folder = Path.cwd() / SKILLS_SUBDIR
    path = folder / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"skill {name!r} not found at {path}")
    _, _, body = _parse_skill(path)
    return body
