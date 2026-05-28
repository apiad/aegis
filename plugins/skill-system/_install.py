"""Setup hook for skill-system: create the skills folder + starter README."""
from __future__ import annotations

from aegis.plugins import InstallContext


SKILLS_README = """\
# .aegis/skills/

Drop Claude-Code-compatible skill files here. Each file is YAML
frontmatter (name + description) followed by markdown body:

```
---
name: my-skill
description: When to reach for this skill.
---

(Skill body in markdown.)
```

The `skill-system` plugin's `pre_turn` hook lists every skill in this
folder as a numbered menu prepended to the harness conversation.
The agent calls `load_skill(name)` to pull the full body when relevant.
"""


def install(ctx: InstallContext) -> None:
    skills_dir = ctx.aegis_dir / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    readme = skills_dir / "README.md"
    if not readme.exists():
        readme.write_text(SKILLS_README, encoding="utf-8")
    n_skills = sum(
        1 for p in skills_dir.glob("*.md") if p.name != "README.md"
    )
    if ctx.console is not None:
        ctx.console.print(
            f"[green]skill-system[/] ready — {n_skills} skill file(s) "
            f"at {skills_dir}/"
        )
