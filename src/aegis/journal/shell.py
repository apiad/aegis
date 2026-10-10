"""Read what a Bash call did from its command and its output.

Best effort, by design: a command that does not parse yields nothing, never an
error. Any static reading of a shell command is a guess, so the journal treats
these as hints and finds a file written this way again through the commit that
records it.
"""

from __future__ import annotations

import os
import re
import shlex

COMMIT = re.compile(
    r"^\[(?P<branch>[^\]]+?)(?: \(root-commit\))? (?P<hash>[0-9a-f]{7,40})\] (?P<subject>.+)$",
    re.M,
)
PR_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+")
# Remove only heredoc body (lines after marker, through terminator), keep marker line.
HEREDOC = re.compile(r"(<<-?\s*(['\"]?)(\w+)\2[^\n]*)\n.*?\n\s*\3[ \t]*(?=\n|$)", re.S)
SEPARATORS = {"&&", "||", ";", "|", "&", ";;", "|&"}


def _body(command: str) -> str:
    """The command without its leading comment lines (the row's name here)."""
    lines = command.splitlines()
    while lines and lines[0].lstrip().startswith("#"):
        lines.pop(0)
    return "\n".join(lines)


def _resolve(path: str, base: str) -> str:
    return os.path.normpath(os.path.join(base, os.path.expanduser(path)))


def _skip_preamble(argv: list[str]) -> int:
    """Return the index of the actual command, skipping VAR=value and leading parens."""
    i = 0
    while i < len(argv):
        word = argv[i]
        # Strip leading paren
        if word.startswith("("):
            word = word[1:]
        # Skip VAR=value assignments
        if "=" in word and not word.startswith("-"):
            i += 1
            continue
        break
    return i


def _commands(body: str) -> list[list[str]]:
    """Each simple command's words, split at && || ; | and newlines."""

    def _strip_parens(argv: list[str]) -> list[str]:
        """Remove leading and trailing parens."""
        while argv and argv[0] in ("(", ")"):
            argv.pop(0)
        while argv and argv[-1] in ("(", ")"):
            argv.pop()
        return argv

    out: list[list[str]] = []
    # Replace heredoc with just the marker line (group 1) to preserve redirects
    for line in HEREDOC.sub(r"\1", body).splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            continue
        cur: list[str] = []
        for t in tokens:
            if t in SEPARATORS:
                if cur:
                    cur = _strip_parens(cur)
                    if cur:
                        out.append(cur)
                cur = []
            else:
                cur.append(t)
        if cur:
            cur = _strip_parens(cur)
            if cur:  # Only append if non-empty after stripping
                out.append(cur)
    return out


def commits(command: str, output: str) -> list[tuple[str, str, str]]:
    if not re.search(r"\bgit\b", command):
        return []
    return [
        (m["branch"], m["hash"], m["subject"].strip()) for m in COMMIT.finditer(output)
    ]


def workdir(command: str, cwd: str) -> str:
    base = cwd
    for argv in _commands(_body(command)):
        if not argv:
            continue
        # Skip leading VAR=value assignments
        start = _skip_preamble(argv)
        if start >= len(argv):
            continue
        cmd = argv[start]
        if cmd == "cd" and len(argv) > start + 1:
            base = _resolve(argv[start + 1], base)
        elif cmd == "git":
            # Look for -C flag in git's leading options (before subcommand).
            # -C is a git option only if it comes before any git subcommand words.
            # Git subcommands are the first non-option, non-flag-arg word.
            # To avoid matching subcommand options like 'commit -C HEAD',
            # we only accept -C if its argument looks like a path.
            for i in range(start + 1, len(argv) - 1):
                if argv[i] == "-C":
                    dir_arg = argv[i + 1]
                    # Accept -C if arg looks like a path (/, ., ~, ..) or absolute
                    if not dir_arg.startswith("-") and (
                        dir_arg.startswith(("/", ".", "~")) or dir_arg.startswith("..")
                    ):
                        return _resolve(dir_arg, base)
            return base
    return base


def writes(command: str, cwd: str) -> list[str]:
    base, out = cwd, set()
    for argv in _commands(_body(command)):
        if not argv:
            continue
        # Skip leading VAR=value assignments
        start = _skip_preamble(argv)
        if start >= len(argv):
            continue
        cmd = argv[start]
        if cmd == "cd" and len(argv) > start + 1:
            base = _resolve(argv[start + 1], base)
            continue
        # Check for redirects
        for k, t in enumerate(argv[:-1]):
            if t in (">", ">>") and not argv[k + 1].startswith("/dev/"):
                out.add(_resolve(argv[k + 1], base))
        words = [w for w in argv if w not in (">", ">>")]
        if not words:
            continue
        args = [w for w in words[1:] if not w.startswith("-")]
        if words[0] == "tee":
            out.update(_resolve(a, base) for a in args)
        elif words[0] == "sed" and any(w.startswith("-i") for w in words[1:]) and args:
            out.add(_resolve(args[-1], base))
        elif words[0] in ("mv", "cp") and len(args) >= 2:
            out.add(_resolve(args[-1], base))
    return sorted(out)


def _flag(rest: str, *names: str) -> str | None:
    try:
        words = shlex.split(rest)
    except ValueError:
        return None
    for k, w in enumerate(words[:-1]):
        if w in names:
            return words[k + 1]
    return None


def pull_request(command: str, output: str) -> str | None:
    m = re.search(r"\bgh\s+pr\s+(create|merge)\b(.*)", _body(command), re.S)
    if not m:
        return None
    verb, rest = m[1], m[2]
    if verb == "create":
        url = PR_URL.search(output)
        if not url:
            return None
        title = _flag(rest, "--title", "-t")
        return f"opened {url[0]}" + (f" · {title}" if title else "")
    n = re.search(r"#(\d+)", output) or re.search(r"\bmerge\s+(\d+)", m[0])
    return f"merged #{n[1]}" if n else "merged a pull request"
