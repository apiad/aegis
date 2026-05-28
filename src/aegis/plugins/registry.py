"""Plugin registry URL parsing + resolution + fetch."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryURL:
    scheme: str               # "gh" or "file"
    owner:  str = ""          # only for gh:
    repo:   str = ""          # only for gh:
    ref:    str = "main"      # only for gh:
    path:   str = ""          # subpath (gh:) or filesystem path (file:)


_GH_RE = re.compile(
    r"^gh:(?P<owner>[^/@#]+)/(?P<repo>[^@#]+)"
    r"(@(?P<ref>[^#]+))?(#(?P<path>.*))?$"
)


def parse_registry_url(url: str) -> RegistryURL:
    if url.startswith("gh:"):
        m = _GH_RE.match(url)
        if not m:
            raise ValueError(f"malformed gh URL: {url}")
        return RegistryURL(
            scheme="gh",
            owner=m["owner"],
            repo=m["repo"],
            ref=m["ref"] or "main",
            path=m["path"] if m["path"] is not None else "plugins/",
        )
    if url.startswith("file://"):
        return RegistryURL(scheme="file", path=url[len("file://"):])
    raise ValueError(
        f"unsupported registry URL {url!r}: "
        "must start with gh: or file://"
    )
