from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    claim_id: str
    handle: str
    prefixes: frozenset[str]   # each ends with "/"
    files: frozenset[str]      # exact paths, no trailing "/"
    intent: str                # "shared" | "exclusive"
    desc: str
    since: str                 # ISO-8601
    host: str = "local"        # which machine these paths are on


def _file_under_prefix(path: str, prefix: str) -> bool:
    # prefix always ends with "/"; the file is "under" it iff it starts with it.
    return path.startswith(prefix)


def claims_overlap(a: Claim, b: Claim) -> bool:
    # Paths only mean something relative to a machine. The same string
    # names a different file on a different host, so claims on different
    # hosts can never overlap — treating them as though they could would
    # both block honest work and pretend to protect files it does not.
    if a.host != b.host:
        return False
    # file ∩ file
    if a.files & b.files:
        return True
    # a file of one under a prefix of the other (both directions)
    for f in a.files:
        if any(_file_under_prefix(f, p) for p in b.prefixes):
            return True
    for f in b.files:
        if any(_file_under_prefix(f, p) for p in a.prefixes):
            return True
    # prefix under prefix (both directions); trailing "/" makes it slash-safe
    for pa in a.prefixes:
        for pb in b.prefixes:
            if pa.startswith(pb) or pb.startswith(pa):
                return True
    return False
