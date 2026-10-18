"""``aegis map``: the views of the code map, one subcommand each.

Each command indexes the git repository holding ``--repo`` (by default the
shell's directory), refreshing what changed, then prints one view. It needs no
server. A line on stderr reports a re-index that parsed files, and files skipped
because their grammar could not be had.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="A map of a git repository: its layout, outlines, callers and impact.",
)

_REPO = typer.Option(
    None, "--repo", help="A path inside the repository; default: here."
)


def cache_dir() -> Path:
    """Shared by every session of this user, so one index serves them all."""
    return (
        Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        / "aegis"
        / "map"
    )


def _index(repo: Path | None):
    from ..cli import path_here
    from .index import Index, repo_root

    try:
        root = repo_root(Path(path_here(str(repo or "."))))
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    ix = Index(root, cache_dir())
    s = ix.stats
    if s.parsed:
        typer.echo(f"[indexed {s.parsed} of {s.files} files in {s.seconds}s]", err=True)
    if s.failed:
        langs = ", ".join(f"{n} {lang}" for lang, n in s.failed.most_common())
        typer.echo(f"[skipped files with no grammar: {langs}]", err=True)
    return ix


@app.command("repo")
def repo(
    path: str = typer.Argument("", help="A folder, relative to the repository root."),
    depth: int = typer.Option(1, help="Folder levels to expand."),
    repo: Path | None = _REPO,
) -> None:
    """Folders and files with their definition counts and first doc lines."""
    from . import views

    typer.echo(views.repo(_index(repo), path, depth))


@app.command("deps")
def deps(
    path: str = typer.Argument("", help="Only files under this folder."),
    repo: Path | None = _REPO,
) -> None:
    """Which files use which, through sure edges, with how many uses."""
    from . import views

    typer.echo(views.deps(_index(repo), path))


@app.command("outline")
def outline(
    path: str = typer.Argument(
        ..., help="A file or folder, relative to the repository root."
    ),
    repo: Path | None = _REPO,
) -> None:
    """Classes and functions: line, signature and first doc line."""
    from . import views

    typer.echo(views.outline(_index(repo), path))


@app.command("find")
def find(
    name: str = typer.Argument(..., help="A name, Class.method or path:name."),
    repo: Path | None = _REPO,
) -> None:
    """Where a name is defined."""
    from . import views

    typer.echo(views.find(_index(repo), name))


@app.command("show")
def show(
    symbol: str = typer.Argument(..., help="A name, Class.method or path:name."),
    repo: Path | None = _REPO,
) -> None:
    """The source of one definition."""
    from . import views

    typer.echo(views.show(_index(repo), symbol))


@app.command("callers")
def callers(
    symbol: str = typer.Argument(..., help="A name, Class.method or path:name."),
    depth: int = typer.Option(1, help="Levels of callers of callers."),
    repo: Path | None = _REPO,
) -> None:
    """Every call of a definition, grouped by file."""
    from . import views

    typer.echo(views.callers(_index(repo), symbol, depth))


@app.command("callees")
def callees(
    symbol: str = typer.Argument(..., help="A name, Class.method or path:name."),
    repo: Path | None = _REPO,
) -> None:
    """What a definition calls."""
    from . import views

    typer.echo(views.callees(_index(repo), symbol))


@app.command("impact")
def impact(
    symbol: str = typer.Argument(..., help="A name, Class.method or path:name."),
    depth: int = typer.Option(3, help="Levels of dependents to follow."),
    repo: Path | None = _REPO,
) -> None:
    """Every definition that uses this one, calls or not, and what uses those."""
    from . import views

    typer.echo(views.callers(_index(repo), symbol, depth, calls_only=False))
