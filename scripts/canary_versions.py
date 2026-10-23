"""The harness versions a canary run installs, and how each compares with the
range this release was tested with (src/aegis/data/tested-harnesses.toml).

    uv run python scripts/canary_versions.py specs latest|min|max
    uv run python scripts/canary_versions.py judge claude-code "2.1.306 (Claude Code)"

``specs`` prints the npm package specs to install, one per line: ``latest``
installs what a person gets today, which is what the nightly run is for;
``min`` and ``max`` install the range's ends, which is how the range is
established before a release (know-how/releasing.md). ``judge`` prints one
word, ``in_range``, ``newer``, ``older`` or ``unknown``, so a pass on a newer
version is read as the evidence for moving the range.
"""

from __future__ import annotations

import sys

from aegis.harness_versions import judge, ranges

MODES = ("latest", "min", "max")
# The harnesses the canary installs from npm. Cursor has a range too, but its
# cursor-agent comes from Cursor's installer, and the npm package of that name
# is unrelated: asking npm for the range's version is a 404.
CANARY = ("claude-code", "codex", "opencode")


def specs(mode: str) -> list[str]:
    if mode not in MODES:
        raise SystemExit(f"versions must be one of {', '.join(MODES)}, not {mode!r}")
    return [
        f"{r.package}@{'latest' if mode == 'latest' else getattr(r, mode + '_s')}"
        for r in ranges().values()
        if r.harness in CANARY
    ]


def main(argv: list[str]) -> None:
    if argv[:1] == ["specs"] and len(argv) == 2:
        print("\n".join(specs(argv[1])))
    elif argv[:1] == ["judge"] and len(argv) == 3:
        print(judge(argv[1], argv[1], argv[2], None, {}).state)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
