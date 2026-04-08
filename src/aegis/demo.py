import json
import logging
import os
from datetime import date
from pathlib import Path
from textwrap import dedent
from pydantic import BaseModel

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

{''.join(f'- [[{link}]]\n' for link in self.related) if self.related else 'No related notes.'}
"""

        target_file.write_text(frontmatter)
        return str(target_file)


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
        f"Based on the desired note content, search the project folder for existing notes "
        f"that might be related. Determine which notes to link using obsidian [[links]]."
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
        response_type=NoteData
    ) as attempt:
        async for result in attempt:
            path = result.save(ctx.cwd)
            await ctx.step(
                f"Note created successfully at: {path}\n\nShow this path to the user."
            )
            return

    await ctx.step("Max retries exceeded. Note was not created. STOP NOW.")


def main():
    server.run(transport="http", host="127.0.0.1", port=4243)
