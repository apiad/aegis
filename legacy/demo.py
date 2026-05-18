import json
import logging
import subprocess
from datetime import date
from pathlib import Path
from textwrap import dedent
from typing import Literal
from pydantic import BaseModel, Field

from aegis import AegisServer, WorkflowContext


logger = logging.getLogger("aegis")


server = AegisServer()


class NoteData(BaseModel):
    title: str
    content: str
    tags: list[str] = []
    folder: str
    filename: str
    related: list[str] = []

    def save(self, cwd: str) -> str:
        base = Path(cwd)

        target_dir = base / self.folder
        if not target_dir.exists() or not target_dir.is_dir():
            raise ValueError(f"Folder does not exist: {self.folder}")

        if not self.folder.startswith("."):
            try:
                target_dir.relative_to(base)
            except ValueError:
                raise ValueError(
                    f"Folder must be within current directory: {self.folder}"
                )

        target_file = target_dir / self.filename
        if not target_file.suffix:
            target_file = target_file.with_suffix(".md")
        else:
            target_file = target_file.with_suffix(".md")

        if target_file.exists():
            raise ValueError(f"File already exists: {target_file}")

        frontmatter = f"""---
title: {self.title}
date: {date.today().isoformat()}
tags: {json.dumps(self.tags)}
related: {json.dumps(self.related)}
---

# {self.title}

{self.content}

## Related Notes

{"".join(f"- [[{link}]]\n" for link in self.related) if self.related else "No related notes."}
"""

        target_file.write_text(frontmatter)
        return str(target_file)


class FileChange(BaseModel):
    path: str
    status: str  # M, A, D, R
    diff: str | None = None


class CommitProposal(BaseModel):
    type: Literal["feat","fix","docs","style","refactor","test","chore","perf","ci","build","revert"]
    scope: str | None = None
    description: str = Field(..., min_length=10)
    files: list[str] = Field(..., min_length=1)


class CommitPlan(BaseModel):
    clusters: list[CommitProposal]


def run_git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def get_git_status(cwd: str) -> list[FileChange]:
    result = run_git(cwd, "status", "--porcelain")
    changes: list[FileChange] = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        status_code: str = line[:2]
        filepath = line[3:] if len(line) > 3 else line[2:]
        status_map = {
            "M ": "modified",
            "M?": "untracked",
            "A ": "added",
            "D ": "deleted",
            "R ": "renamed",
            "??": "untracked",
        }
        changes.append(FileChange(
            path=filepath,
            status=status_map.get(status_code, status_code.strip()),
        ))
    return changes


def git_add_commit(cwd: str, files: list[str], message: str) -> None:
    run_git(cwd, "add", *files)
    run_git(cwd, "commit", "-m", message)


@server.prompt()
def init() -> str:
    """Initialize the connection and get a greeting."""
    return dedent(
        """
        Welcome to Aegis!
        """
    )


@server.workflow()
async def onboard(ctx: WorkflowContext):
    """Explore the project and onboard the user."""

    await ctx.step(
        "You are onboarding into a new project. Your task is to explore the codebase and identify "
        "interesting files that can help you understand the project."
        "Read a few key files to understand the project structure."
    )

    await ctx.step(
        "Reply to the user with a comprehensive summary covering:\n"
        "1. What the project is (purpose)\n"
        "2. How it's organized (main directories, key files)\n"
        "3. How to run it\n"
        "4. Current development status.\n\n"
    )


@server.workflow()
async def note(ctx: WorkflowContext):
    """Create a structured obsidian note in the project."""
    await ctx.step(
        "Determine what would the user like to note. "
        "Exlore the content, topic, or key points he would "
        "like to capture. "
        "The user can provide rough notes, a topic, or just key points."
    )

    await ctx.step(
        "Based on the desired note content, search the project folder for existing notes "
        "that might be related. Determine which notes to link using obsidian [[links]]."
    )

    async with ctx.attempt(
        "Now compose the final note content. Include:\n"
        "- title: A clear, descriptive title\n"
        "- folder: The target folder (must exist, e.g., 'notes/', 'docs/')\n"
        "- filename: The filename (without .md extension)\n"
        "- content: The note content in markdown\n"
        "- tags: A list of relevant tags\n"
        "- related: List of [[obsidian links]] to related notes\n"
        "\nValidation: folder must exist, filename must not already exist.\n\n"
        "DO NOT WRITE THE NOTE TO DISK, just compose the above information as a message.",
        response_type=NoteData,
    ) as attempt:
        async for result in attempt:
            path = result.save(ctx.cwd)
            await ctx.step(
                f"Note created successfully at: {path}\n\nShow this path to the user."
            )
            return

    await ctx.step("Max retries exceeded. Note was not created. STOP NOW.")


@server.workflow()
async def fail(ctx: WorkflowContext):
    """A workflow that always fails for testing error handling."""
    await ctx.step("This workflow will fail intentionally...")
    raise ValueError("Intentional test failure")


@server.workflow()
async def commit(ctx: WorkflowContext):
    """Orchestrate git commits from working tree changes."""
    changes = get_git_status(ctx.cwd)
    if not changes:
        await ctx.step("Working tree is clean. Nothing to commit.")
        return

    async with ctx.attempt(
        "Analyze the following changes and propose commits:\n"
        + "\n".join(f"- {c.path} ({c.status})" for c in changes)
        + "\n\nGroup these files into meaningful commits using "
        "conventional commits format. Inform and get user consent before next step, iterate with user until consent.",
        response_type=CommitPlan,

    ) as attemps:
        async for plan in attemps:
            changes_paths = {c.path for c in changes}
            proposed_paths: set[str] = set()

            for proposal in plan.clusters:
                proposed_paths.update(proposal.files)

            missing = changes_paths - proposed_paths
            if missing:
                raise ValueError(
                    f"Validation failed: The following files are missing from all proposals: {missing}"
                )

            for proposal in plan.clusters:
                try:
                    git_add_commit(
                        ctx.cwd,
                        proposal.files,
                        f"{proposal.type}{'(' + proposal.scope + ')' if proposal.scope else ''}: {proposal.description}",
                    )
                except Exception as e:
                    logger.warning(f"Commit failed for {proposal.files}: {e}")
                    continue

    changes = get_git_status(ctx.cwd)

    if not changes:
        await ctx.step("Inform the user all commits where done.")
    else:
        await ctx.step("Inform the user some files couldn't be commited. Suggest to rerun workflow.")


def main():
    server.run(transport="http", host="127.0.0.1", port=4243)
