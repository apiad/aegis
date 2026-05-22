"""review_branch — fan out reviewers in parallel over the working diff.

Computes the diff vs a base ref via ``git_helpers.diff_vs``, spawns one
reviewer subagent per configured profile, joins their results, and writes
a structured markdown report under ``docs/reviews/``.
"""
from __future__ import annotations

from pathlib import Path

from aegis.workflow import workflow
from aegis.workflows._lib.git_helpers import branch_slug, diff_vs
from aegis.workflows._lib.spec_renderer import today_iso

_DEFAULT_REVIEWERS = ["security-reviewer", "api-reviewer", "test-reviewer"]


def _review_prompt(profile: str, diff: str) -> str:
    return (f"You are the {profile}. Review the following diff and report "
            f"findings. Keep it concise.\n\n```diff\n{diff}\n```")


def _render_review_report(diff: str,
                          results: list[tuple[str, str]]) -> str:
    lines = [f"# Review — {branch_slug()}", "",
             f"_Generated {today_iso()}._", ""]
    for profile, body in results:
        lines += [f"## {profile}", "", body.strip(), ""]
    return "\n".join(lines)


def _resolve(engine, rel_path: str) -> Path:
    base = engine.config.get("cwd") if engine.config else None
    if base is None:
        from aegis.config import find_project_root
        base = str(find_project_root() or Path.cwd())
    return Path(base) / rel_path


@workflow("review_branch")
async def review_branch(engine, *, base: str = "main") -> str:
    diff = diff_vs(base)
    if not diff.strip():
        return "no diff vs base"

    reviewers = engine.config.get("reviewers", _DEFAULT_REVIEWERS)

    async def one_review(profile: str) -> tuple[str, str]:
        alias = f"r-{profile.split('-')[0]}"
        handle = await engine.spawn(profile, alias=alias)
        try:
            reply = await engine.send(handle, _review_prompt(profile, diff))
            return profile, reply
        finally:
            await engine.close(handle)

    results = await engine.parallel([one_review(p) for p in reviewers])
    report = _render_review_report(diff, results)

    rel = f"docs/reviews/{today_iso()}-{branch_slug()}.md"
    out = _resolve(engine, rel)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    engine.log(f"Review written to {rel}")
    return rel
