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
MERGED = re.compile(r"[Mm]erged pull request #?(\d+)")
# The issues a PR body says it closes, as GitHub reads them.
CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?) #(\d+)", re.I)
ISSUE_CLOSE = re.compile(r"\bgh\s+issue\s+close\s+(?:\S*/issues/)?#?(\d+)\b")
# How many lines a quote may span: past it a line with a stray quote is dropped
# at once, instead of being re-read joined with every later line.
JOIN_WINDOW = 100
SEPARATORS = {"&&", "||", ";", "|", "&", ";;", "|&"}
# What git commit says when it made no commit, -q or not.
NO_COMMIT = re.compile(r"nothing (added )?to commit|no changes added to commit")
# -m or -F, alone or last in a cluster of short flags (-qm, -am), with its value
# in the same word or the next.
SHORT_MESSAGE = re.compile(r"^-[a-zA-Z]*?(?P<flag>[mF])(?P<value>.*)$", re.S)
COMMIT_LINE = re.compile(r"\bgit\b.*\bcommit\b")
# git subcommands that print "[branch hash] subject" for a commit they make.
COMMITTERS = {"commit", "cherry-pick", "revert"}


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


def _git_dir(argv: list[str], base: str) -> tuple[str, str]:
    """The directory a `git ...` command runs in, and its subcommand: each -C
    among git's options (before the subcommand) moves it; the first word not
    starting with "-" is the subcommand and ends the scan."""
    k = 1
    while k < len(argv):
        w = argv[k]
        if w == "-C" and k + 1 < len(argv):
            base = _resolve(argv[k + 1], base)
            k += 2
        elif w in (
            "-c",
            "--git-dir",
            "--work-tree",
            "--namespace",
            "--exec-path",
        ) and k + 1 < len(argv):
            k += 2
        elif w.startswith("-"):
            k += 1
        else:
            return base, w
    return base, ""


def _commands(body: str) -> list[list[str]]:
    """Each simple command's words, split at && || ; | and newlines."""

    def _strip_parens(argv: list[str]) -> list[str]:
        """Remove leading and trailing parens."""
        while argv and argv[0] in ("(", ")"):
            argv.pop(0)
        while argv and argv[-1] in ("(", ")"):
            argv.pop()
        return argv

    def _split(text: str) -> list[str] | None:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            return list(lexer)
        except ValueError:
            return None

    out: list[list[str]] = []
    # Replace heredoc with just the marker line (group 1) to preserve redirects
    lines = HEREDOC.sub(r"\1", body).splitlines()
    k = 0
    while k < len(lines):
        # A line that opens a quote a later line closes (a multi-line -m, or
        # "$(cat <<'EOF' ... EOF\n)") is read joined with the lines it needs.
        for j in range(k, min(k + JOIN_WINDOW, len(lines))):
            tokens = _split("\n".join(lines[k : j + 1]))
            if tokens is not None:
                break
        else:
            k += 1  # never parses: drop this line alone
            continue
        k = j + 1
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


def commits(
    command: str, output: str, is_error: bool = False
) -> list[tuple[str, str, str]]:
    """(branch, hash, subject) of each commit the call made. A commit that
    printed no line of its own (`git commit -q`, a pipe) comes from its command,
    with branch and hash empty, unless the call failed or said nothing changed."""
    if not re.search(r"\bgit\b", command):
        return []
    found = [
        (m["branch"], m["hash"], m["subject"].strip()) for m in COMMIT.finditer(output)
    ]
    if found or is_error or NO_COMMIT.search(output):
        return found
    return [("", "", subject) for subject in _commit_subjects(_body(command))]


def _message_arg(words: list[str]) -> str | None:
    """The first message a `git commit`'s arguments give: -m's text, or "-" for
    -F -; None when they give none this can read."""
    for k, w in enumerate(words):
        if w.startswith("--message="):
            return w.split("=", 1)[1]
        if w.startswith("--file="):
            return w.split("=", 1)[1] if w == "--file=-" else None
        if w in ("--message", "--file"):
            value = words[k + 1] if k + 1 < len(words) else None
            return value if w == "--message" or value == "-" else None
        short = SHORT_MESSAGE.match(w)
        if short:
            value = short["value"] or (words[k + 1] if k + 1 < len(words) else None)
            return value if short["flag"] == "m" or value == "-" else None
    return None


def _commit_subjects(body: str) -> list[str]:
    """The subject of each `git commit` in the command that names its message.
    A heredoc's lines are gone from what _commands reads, so those on a commit's
    own line are kept apart, in order."""
    heredocs = [
        m[0][len(m[1]) + 1 :].splitlines()[:-1]
        for m in HEREDOC.finditer(body)
        if COMMIT_LINE.search(body[: m.start()].rsplit("\n", 1)[-1])
    ]
    out = []
    for argv in _commands(body):
        argv = argv[_skip_preamble(argv) :]
        if not argv or argv[0] != "git":
            continue
        _, sub = _git_dir(argv, "")
        if sub != "commit":
            continue
        words = argv[argv.index(sub) + 1 :]
        if "--dry-run" in words:
            continue
        message = _message_arg(words)
        if message is None:
            continue
        lines: list[str] = []
        if message == "-" or "<<" in message:
            lines = heredocs.pop(0) if heredocs else []
        elif not message.startswith("$"):
            lines = message.splitlines()
        subject = next((x.strip() for x in lines if x.strip()), "")
        if subject:
            out.append(subject)
    return out


def _gits(command: str, cwd: str) -> list[tuple[str, str]]:
    """(directory, subcommand) of each git command, following cd's on the way."""
    base, out = cwd, []
    for argv in _commands(_body(command)):
        start = _skip_preamble(argv)
        if start >= len(argv):
            continue
        cmd = argv[start]
        if cmd == "cd" and len(argv) > start + 1:
            base = _resolve(argv[start + 1], base)
        elif cmd == "git":
            d, sub = _git_dir(argv[start:], base)
            out.append((d, sub))
    return out


def workdir(command: str, cwd: str) -> str:
    """The directory of the command's first git command, or where its cd's
    leave it when it runs none."""
    gits = _gits(command, cwd)
    if gits:
        return gits[0][0]
    base = cwd
    for argv in _commands(_body(command)):
        start = _skip_preamble(argv)
        if start < len(argv) - 1 and argv[start] == "cd":
            base = _resolve(argv[start + 1], base)
    return base


def commit_dirs(command: str, cwd: str, n: int) -> list[str]:
    """The directory of each of the n commits the output printed: the k-th
    commit command's when the counts pair, else the last commit command's,
    else the first git command's (a script that committed)."""
    dirs = [d for d, sub in _gits(command, cwd) if sub in COMMITTERS]
    if len(dirs) == n:
        return dirs
    return [dirs[-1] if dirs else workdir(command, cwd)] * n


def leading_cd(command: str, cwd: str) -> str | None:
    """Where a shell that keeps its directory between calls (Claude's Bash) is
    left by the command's leading cd's, or None when it starts with none. A
    subshell's cd, a cd after other work and a target with a variable are not
    followed."""
    body = _body(command)
    if body.lstrip().startswith("("):
        return None
    base, moved = cwd, False
    for argv in _commands(body):
        if len(argv) != 2 or argv[0] != "cd" or "$" in argv[1] or argv[1] == "-":
            break
        base, moved = _resolve(argv[1], base), True
    return base if moved else None


def writes(command: str, cwd: str) -> list[str]:
    # base is None after a cd to a variable: relative targets are then unknown.
    base: str | None = cwd
    out = set()
    for argv in _commands(_body(command)):
        if not argv:
            continue
        # Skip leading VAR=value assignments
        start = _skip_preamble(argv)
        if start >= len(argv):
            continue
        cmd = argv[start]
        if cmd == "cd" and len(argv) > start + 1:
            target = argv[start + 1]
            if "$" in target:
                base = None
            elif base is not None or os.path.isabs(os.path.expanduser(target)):
                base = _resolve(target, base or os.sep)
            continue
        # Check for redirects
        found: list[str] = []
        for k, t in enumerate(argv[:-1]):
            if t in (">", ">>") and not argv[k + 1].startswith("/dev/"):
                found.append(argv[k + 1])
        words = [w for w in argv[start:] if w not in (">", ">>")]
        args = [w for w in words[1:] if not w.startswith("-")]
        if not words:
            pass
        elif words[0] == "tee":
            found += args
        elif words[0] == "sed" and any(w.startswith("-i") for w in words[1:]) and args:
            found.append(args[-1])
        elif words[0] in ("mv", "cp") and len(args) >= 2:
            found.append(args[-1])
        # A target named by a variable is not a path this can know.
        out.update(
            _resolve(p, base or os.sep)
            for p in found
            if "$" not in p
            and (base is not None or os.path.isabs(os.path.expanduser(p)))
        )
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


def pull_request(command: str, output: str, is_error: bool = False) -> str | None:
    """The PR row a `gh pr create` or `gh pr merge` call stands for. A call that
    errored is a row only for a merge gh's own output says happened (it can exit
    non-zero after merging); a rejected merge or a create that found its PR
    already open did nothing."""
    m = re.search(r"\bgh\s+pr\s+(create|merge)\b(.*)", _body(command), re.S)
    if not m:
        return None
    verb, rest = m[1], m[2]
    if verb == "create":
        if is_error:
            return None
        url = PR_URL.search(output)
        if not url:
            return None
        title = _flag(rest, "--title", "-t")
        return f"opened {url[0]}" + (f" · {title}" if title else "")
    if is_error:
        done = MERGED.search(output)
        return f"merged #{done[1]}" if done else None
    n = re.search(r"#(\d+)", output) or re.search(r"\bmerge\s+(\d+)", m[0])
    return f"merged #{n[1]}" if n else "merged a pull request"


def issue_closes(command: str, output: str, is_error: bool) -> list[int]:
    """The issue a `gh issue close` call closed, by number or URL."""
    if is_error:
        return []
    m = ISSUE_CLOSE.search(_body(command))
    return [int(m[1])] if m else []


def closing_refs(command: str) -> list[int]:
    """The issues a `gh pr create` names as closed in its --body; a
    --body-file is not read."""
    m = re.search(r"\bgh\s+pr\s+create\b(.*)", _body(command), re.S)
    body = _flag(m[1], "--body", "-b") if m else None
    return [int(n) for n in CLOSING.findall(body or "")]
