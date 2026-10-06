"""Building the prompt a queue worker receives.

Three parts in a fixed order: a preamble the code owns, the card, and the
contract the code owns. The contract is last so nothing the card or a future
agent-written brief says can come after it.
"""

from __future__ import annotations

from aegis.workflows.builtins.afk.board import Card

CONTRACT = """
## How to report

The last thing you say must be exactly one fenced `aegis-report` block, and
nothing after it. A run nobody can parse is a run that did not demonstrably
happen.

```aegis-report
status: needs-review | blocked | failed
summary: one line, imperative, what changed
gate: <the command you ran> -> <its exit code>
artifacts:
  - a pull request URL, a commit sha, or a path
changed: <number of files>
judgement:
  - each call you made where the card did not say what to do
notes: |
  anything else worth knowing
```

There is no `done`. The furthest you may claim is `needs-review`.

`gate` records the command and the exit code **you saw**. The coordinator
runs the same command itself and compares. Report what happened, including a
red gate: a truthful `failed` is worth more than a green claim that does not
survive one re-run.
""".strip()


def _fence_off(body: str) -> str:
    """Neutralise fenced blocks inside a card body.

    A card describing this feature quotes the report format. Passed through
    verbatim it hands the worker two templates, and the parser two blocks.
    The separator is a zero-width space: it breaks the fence for every parser
    while reading as the original text.
    """
    return (body or "").replace("```", "``​`")


def compose(
    card: Card,
    *,
    repo_path: str,
    branch: str,
    gate_cmd: str,
    brief: str = "",
) -> str:
    gate_line = (
        f"The gate for this repo is `{gate_cmd}`. Run it before you report."
        if gate_cmd != "none"
        else "This repo declares no gate target. Report `gate: none`; do not "
        "invent one and do not report a green gate you did not run."
    )
    parts = [
        f"""You are working one card from an unattended task board. Nobody is
watching, so finish or say plainly why you could not.

**Repository:** `{repo_path}` (branch `{branch}`)
**Card:** #{card.number} — {card.title}
**Issue:** {card.url}

Read `AGENTS.md` in that repository first, then its `know-how/` docs for the
job in front of you, and follow them. They decide what this task should
produce — a branch and a pull request, a commit, a document. That is not the
coordinator's call and it is not stated here.

{gate_line}

**Keep a task list from your first turn.** Write out your plan through your
harness's own task list, one item per step, and keep it current as you go.
aegis mirrors that list onto the card every two minutes, so it is how anyone
sees what you are doing without attaching to your session. It costs you
nothing and it is the only progress signal there is.""",
        f"## The task\n\n{_fence_off(card.body)}",
    ]
    if brief.strip():
        parts.append(f"## Context from the coordinator\n\n{_fence_off(brief)}")
    parts.append(CONTRACT)
    return "\n\n---\n\n".join(parts)
