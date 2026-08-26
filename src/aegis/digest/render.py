"""TurnFacts as the text block a prompt embeds. Pure."""
from __future__ import annotations

from aegis.digest.models import TurnFacts

MAX_COMMITS_SHOWN = 10


def render_facts(facts: TurnFacts) -> str:
    """A compact, honest block. Never empty.

    Emptiness would read as "facts unavailable", which is a different
    claim from "this turn changed nothing" — and the judge acts on the
    difference between a still turn and an unobserved one.
    """
    lines: list[str] = ["--- what this turn did ---"]
    if facts.error:
        lines.append(f"(facts incomplete: {facts.error})")
    for repo in facts.repos:
        if not repo.available:
            lines.append(f"{repo.name}@{repo.host}: not inspected "
                         f"(off-host)")
            continue
        head = (f"{repo.name}: {len(repo.commits)} commit"
                f"{'' if len(repo.commits) == 1 else 's'}")
        if repo.files_written:
            head += (f", {repo.files_written} file"
                     f"{'' if repo.files_written == 1 else 's'} written")
        lines.append(head)
        for c in repo.commits[:MAX_COMMITS_SHOWN]:
            lines.append(f"  {c.sha} {c.subject}")
        if len(repo.commits) > MAX_COMMITS_SHOWN:
            lines.append(f"  … and {len(repo.commits) - MAX_COMMITS_SHOWN}"
                         f" more")
    if facts.plan_total:
        lines.append(f"plan: {facts.plan_done}/{facts.plan_total} done "
                     f"(+{facts.plan_done_delta} this turn)")
    if len(lines) == 1:
        lines.append("no commits, no files written, no plan movement")
    lines.append("--- end ---")
    return "\n".join(lines)
