"""Load user-authored prompt commands from ``<root>/.aegis/commands/*.md``.

Each file becomes a ``source="user"`` SlashCommand whose handler expands the body
template (see :mod:`aegis.commands.expand`) and returns a ``deliver`` effect so
the seam sends the expansion to the agent as a normal message. Frontmatter:
``description`` → summary, ``argument-hint`` → usage suffix. Boot-load only;
re-callable (idempotent).
"""
from __future__ import annotations

import logging
from pathlib import Path

from ruamel.yaml import YAML

from aegis.commands import (
    REGISTRY, CommandCollision, CommandResult, SlashCommand, register)
from aegis.commands.args import Arg, ArgSpec
from aegis.commands.expand import ExpandError, expand

logger = logging.getLogger(__name__)
_yaml = YAML(typ="safe")

_GREEDY_SPEC = ArgSpec(
    positionals=(Arg("arguments", required=False, greedy=True),))


def _split_frontmatter(raw: str) -> "tuple[dict, str]":
    """Return (frontmatter dict, body). A leading ``---`` fence delimits YAML."""
    if raw.startswith("---"):
        parts = raw.split("\n", 1)
        rest = parts[1] if len(parts) > 1 else ""
        end = rest.find("\n---")
        if end != -1:
            head = rest[:end]
            body = rest[end + 4:]
            if body.startswith("\n"):
                body = body[1:]
            meta = _yaml.load(head) or {}
            return (meta if isinstance(meta, dict) else {}), body
    return {}, raw


def _make_command(name: str, meta: dict, template: str, root: Path,
                  run_shell) -> SlashCommand:
    summary = str(meta.get("description", "") or "")
    hint = meta.get("argument-hint")
    usage = f"/{name} {hint}" if hint else f"/{name}"

    async def _run(ctx, args) -> CommandResult:
        argstr = args.get("arguments", "") or ""
        try:
            text = await expand(template, argstr, root, run_shell)
        except ExpandError as e:
            return CommandResult(False, f"/{name} failed", str(e))
        return CommandResult(True, f"/{name}",
                             effect={"kind": "deliver", "text": text})

    return SlashCommand(name, summary, usage, _run,
                        source="user", spec=_GREEDY_SPEC)


def load_prompt_commands(root: Path, run_shell=None) -> list[str]:
    if run_shell is None:
        from aegis.tui.shell_escape import run_shell_escape
        run_shell = run_shell_escape
    folder = Path(root) / ".aegis" / "commands"
    if not folder.is_dir():
        return []
    loaded: list[str] = []
    for path in sorted(folder.glob("*.md")):
        name = path.stem.lower()
        try:
            meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        except OSError as e:
            logger.warning("prompt command %s unreadable: %s", path, e)
            continue
        cmd = _make_command(name, meta, body, Path(root), run_shell)
        # Idempotent reload: a user command replacing an existing user command
        # of the same name is the same file reloading — drop the old, register.
        existing = REGISTRY.get(name)
        if existing is not None and existing.source == "user":
            REGISTRY.pop(name, None)
        try:
            register(cmd)
            loaded.append(name)
        except CommandCollision as e:
            logger.warning("prompt command /%s skipped: %s", name, e)
    return loaded
