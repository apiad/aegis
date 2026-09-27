"""`python -m aegis.changelog` — check, preview or apply fragment collation.

Three verbs, because they are the three moments this gets used:

    check    every fragment parses. Cheap enough for CI and for `make check`.
    preview  print the collated CHANGELOG to stdout, touching nothing.
    apply    write CHANGELOG.md and delete the fragments. `--version` folds
             them into a new release section instead of [Unreleased].

`apply` is the only one that writes, and it is the only one a releaser runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aegis.changelog import CATEGORIES, FragmentError, collate, read_fragments

FRAGMENT_DIR = Path("changelog.d")
CHANGELOG = Path("CHANGELOG.md")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aegis.changelog")
    ap.add_argument("verb", choices=("check", "preview", "apply"))
    ap.add_argument("--version", help="collate into a new release section")
    ap.add_argument("--date", help="release date; defaults to today (UTC)")
    ap.add_argument("--dir", type=Path, default=FRAGMENT_DIR)
    ap.add_argument("--changelog", type=Path, default=CHANGELOG)
    args = ap.parse_args(argv)

    try:
        fragments = read_fragments(args.dir)
    except FragmentError as e:
        # The whole point of the mechanism is that an entry cannot go missing,
        # so a fragment that does not parse is a failure and not a warning.
        print(f"error: {e}", file=sys.stderr)
        print(f"categories: {', '.join(CATEGORIES)}", file=sys.stderr)
        return 1

    if args.verb == "check":
        print(f"{len(fragments)} fragment(s), all valid")
        return 0

    if args.version and not args.date:
        from datetime import datetime, timezone

        args.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    out = collate(
        args.changelog.read_text(), fragments, version=args.version, date=args.date
    )

    if args.verb == "preview":
        sys.stdout.write(out)
        return 0

    args.changelog.write_text(out)
    for f in sorted(args.dir.glob("*.md")):
        if f.name != "README.md":
            f.unlink()
    where = f"[{args.version}]" if args.version else "[Unreleased]"
    print(f"collated {len(fragments)} fragment(s) into {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
