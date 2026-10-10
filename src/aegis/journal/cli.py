"""``aegis journal``: search what was done on this server, or rebuild the journal
from the transcripts. Both read <state> directly; neither needs the server."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(
    add_completion=False,
    help="Search what was done on this server, or rebuild the journal from the transcripts.",
)
_ROOT = typer.Option(
    None, help="Config root; default: the nearest ancestor holding .aegis.yaml."
)


@app.command("search")
def search(
    pattern: str = typer.Argument("", help="Words to find in the entries' text."),
    since: str = typer.Option(
        None, help="First day: 2026-10-09, today, yesterday or 7d."
    ),
    until: str = typer.Option(None, help="Last day, included."),
    path: str = typer.Option(None, help="Only entries that wrote under this path."),
    session: str = typer.Option(None, help="A handle, past or present, or a log id."),
    kind: list[str] = typer.Option(
        None, help="turn, commit, pr, plan, note or session."
    ),
    limit: int = typer.Option(50, help="At most this many entries."),
    root: Path | None = _ROOT,
) -> None:
    """Entries newest first, grouped by day."""
    from ..cli import roots_here
    from . import db, render
    from .query import build

    roots = roots_here(root)
    file = roots.state_root / "journal.db"
    if not file.is_file():
        typer.echo(
            "no journal here yet: aegis writes it once it serves this root", err=True
        )
        raise typer.Exit(1)
    con = db.open_read(file)
    try:
        q = build(
            con,
            str(roots.config_root),
            since=since,
            until=until,
            pattern=pattern,
            path=path,
            session=session,
            kinds=kind,
            limit=limit,
        )
        hits, cut = db.search(con, q)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from None
    finally:
        con.close()
    typer.echo(render.text(hits, cut, str(roots.config_root)))


@app.command("rebuild")
def rebuild(root: Path | None = _ROOT) -> None:
    """Empty the journal and derive it again from every transcript, archived ones included."""
    from ..cli import roots_here
    from .service import Journal

    n = Journal(roots_here(root).state_root, None).rebuild()
    typer.echo(f"{n} entries")
