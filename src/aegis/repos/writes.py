"""Which tool calls count as a write, and what they wrote to. Pure.

Membership is writes-only. Reads do not promote a repo — otherwise every
repo an agent merely grepped shows up and the section stops meaning *work
is happening here*.

Bash is **not** here, deliberately. Statically parsing a shell command for
write targets is the guess the mandatory-claims spec declined to dress up
as complete; a `sed -i` will not register its repo. Under-reporting is the
right failure: a row that appeared because a heuristic misread a `>` inside
a quoted string would make the whole section untrusted.
"""
from __future__ import annotations

# Claude Code's write tools, by name.
_CLAUDE_WRITES = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# ACP reports a semantic kind rather than a tool name, and every harness on
# that seam uses its own titles ("edit_file", "apply_patch", …). The kind is
# the stable thing.
_ACP_WRITE_KINDS = {"edit", "delete", "move"}

_PATH_KEYS = ("file_path", "notebook_path", "path", "abs_path")


def write_target(name: str, raw_input: dict | None = None,
                 locations: tuple = (), kind: str | None = None) -> str | None:
    """The path this tool call wrote to, or ``None`` if it wrote nothing.

    ``None`` for every read, search, and shell call — including the ones
    that really did write. See the module docstring.
    """
    is_write = name in _CLAUDE_WRITES or (kind in _ACP_WRITE_KINDS)
    if not is_write:
        return None

    inp = raw_input or {}
    for key in _PATH_KEYS:
        value = inp.get(key)
        if isinstance(value, str) and value:
            return value

    # ACP edits carry the path as a location rather than in raw_input.
    if locations:
        path = locations[0][0] if isinstance(locations[0], tuple) \
            else getattr(locations[0], "path", "")
        if path:
            return str(path)
    return None
