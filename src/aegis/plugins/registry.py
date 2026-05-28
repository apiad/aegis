"""Plugin registry URL parsing + resolution + fetch."""
from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


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


@contextlib.contextmanager
def fetch_plugin(url: RegistryURL, *, plugin_name: str) -> Iterator[Path]:
    """Yield a path to a temporary copy of `plugin_name`'s folder from `url`.

    Cleans up the temp dir on context exit.
    """
    tmp = Path(tempfile.mkdtemp(prefix="aegis-plugin-"))
    try:
        if url.scheme == "file":
            src = Path(url.path) / plugin_name
            if not src.exists():
                raise FileNotFoundError(
                    f"plugin {plugin_name!r} not found at {Path(url.path)}"
                )
            dest = tmp / plugin_name
            shutil.copytree(src, dest)
            yield dest
        elif url.scheme == "gh":
            dest = _fetch_gh(url, plugin_name=plugin_name, into=tmp)
            yield dest
        else:
            raise ValueError(f"unsupported scheme {url.scheme!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fetch_gh(url: RegistryURL, *, plugin_name: str, into: Path) -> Path:
    """git archive --remote=<repo> <ref> <path>/<plugin>/ | tar -x -C <tmp>."""
    if shutil.which("git") is None:
        raise RuntimeError(
            "git not available on PATH; needed for gh: registry fetch"
        )
    repo_url = f"https://github.com/{url.owner}/{url.repo}.git"
    subpath = f"{url.path.rstrip('/')}/{plugin_name}"
    archive_cmd = ["git", "archive", f"--remote={repo_url}",
                   url.ref, subpath]
    archive = subprocess.run(archive_cmd, capture_output=True, check=False)
    if archive.returncode != 0 or not archive.stdout:
        # Fallback: shallow clone (GitHub doesn't serve archives over HTTPS).
        return _fetch_gh_clone(url, plugin_name=plugin_name, into=into)
    subprocess.run(
        ["tar", "-x", "-C", str(into)], input=archive.stdout, check=True,
    )
    final = into / subpath
    if not final.exists():
        raise RuntimeError(
            f"git archive succeeded but {subpath} not in archive"
        )
    return final


def _fetch_gh_clone(url: RegistryURL, *, plugin_name: str, into: Path) -> Path:
    """Fallback: shallow clone the repo, then read the plugin folder out."""
    clone_dir = into / "_clone"
    subprocess.run(
        ["git", "clone", "--depth=1", f"--branch={url.ref}",
         f"https://github.com/{url.owner}/{url.repo}.git", str(clone_dir)],
        check=True, capture_output=True,
    )
    src = clone_dir / url.path.rstrip("/") / plugin_name
    if not src.exists():
        raise RuntimeError(
            f"plugin {plugin_name!r} not found in {url.owner}/{url.repo}"
            f" at {url.path}"
        )
    dest = into / plugin_name
    shutil.copytree(src, dest)
    return dest
