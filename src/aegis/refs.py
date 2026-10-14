"""What a span in a reply's backticks may name, and what it names on this
disk (#393). inline_paths keeps the candidates when a reply is built, without
touching the disk, so an archive's links never depend on the machine that
reads it; path.stat asks kind() when the reply is shown."""

import re

# A file: a slash and an extension, with an optional :line (#323).
PATH = re.compile(
    r"(?:~?/)?(?:[\w.@+-]+/)*[\w@+-][\w.@+-]*\.[A-Za-z][A-Za-z0-9]*(?::\d+)?"
)
# A file named at a root without a slash: a capitalised name such as DESIGN.md.
BARE = re.compile(r"[A-Z][A-Z0-9_-]*\.[a-z]+(?::\d+)?")
# A folder or a repo: two or more segments, or one ending in a slash.
SLASHED = re.compile(r"(?:~?/)?[\w.@+-]+(?:(?:/[\w.@+-]+)+/?|/)")
_OWNER = r"[A-Za-z0-9-]{1,39}"
_NAME = r"[A-Za-z0-9._-]{1,100}"
REPO = re.compile(rf"{_OWNER}/{_NAME}")
REPO_N = re.compile(rf"{_OWNER}/{_NAME}#\d+")
GITHUB = re.compile(rf"github\.com/{_OWNER}/{_NAME}(?:/\S*)?")
LINE = re.compile(r":\d+$")
EXT = re.compile(r"\.[A-Za-z][A-Za-z0-9]*$")
# The most kinds path.stat answers for one reply; the rest are plain code.
MAX = 64


def path_shaped(text: str) -> bool:
    """A file's shape: a slash and an extension, or a capitalised name."""
    return bool(("/" in text and PATH.fullmatch(text)) or BARE.fullmatch(text))


def candidate(text: str) -> bool:
    """Whether a span may name a file, a folder or a GitHub repo. A URL is
    markdown-it's to link; a slashed span with no letter (1.5/2.0) is a
    number."""
    if "://" in text:
        return False
    if path_shaped(text) or REPO_N.fullmatch(text) or GITHUB.fullmatch(text):
        return True
    return bool(SLASHED.fullmatch(text)) and re.search(r"[A-Za-z]", text) is not None
