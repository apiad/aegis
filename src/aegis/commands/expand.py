"""Prompt-command template expansion (Claude-Code parity).

Order (args first, so ``!`git log $1``` works):
  1. $ARGUMENTS → raw stripped argstr; $1..$9 → shlex-split tokens (missing → "").
  2. @<path>   → splice file contents (resolved under ``root``); missing → ExpandError.
  3. !`cmd`    → run via the injected async run_shell(cmd, root); inline its output.

``.aegis/commands/*.md`` is trusted local config: @file reads and ``!`cmd``` execute
on expansion. Arg values are substituted before the include/shell scan, so they
can influence includes/shell — accepted inside the trust boundary.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

_FILE_RE = re.compile(r"(?<!\S)@(\S+)")
_SHELL_RE = re.compile(r"!`([^`]*)`")


class ExpandError(ValueError):
    """Human-facing expansion failure (missing @file, etc.)."""


def _split_args(argstr: str) -> list[str]:
    try:
        return shlex.split(argstr)
    except ValueError:
        return argstr.split()


def _sub_args(template: str, argstr: str) -> str:
    raw = argstr.strip()
    toks = _split_args(argstr)
    out = template.replace("$ARGUMENTS", raw)
    for i in range(9, 0, -1):                 # $9..$1 so $1 doesn't eat $12
        val = toks[i - 1] if i - 1 < len(toks) else ""
        out = out.replace(f"${i}", val)
    return out


def _sub_files(text: str, root: Path) -> str:
    def repl(m: re.Match) -> str:
        rel = m.group(1)
        path = root / rel
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            raise ExpandError(
                f"@{rel}: cannot read include ({e.__class__.__name__})")
    return _FILE_RE.sub(repl, text)


async def _sub_shell(text: str, root: Path, run_shell) -> str:
    out: list[str] = []
    last = 0
    for m in _SHELL_RE.finditer(text):
        out.append(text[last:m.start()])
        out.append(await run_shell(m.group(1), root))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


async def expand(template: str, argstr: str, root: Path, run_shell) -> str:
    text = _sub_args(template, argstr)
    text = _sub_files(text, root)
    text = await _sub_shell(text, root, run_shell)
    return text
