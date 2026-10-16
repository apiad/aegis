"""Saved workflows (docs/superpowers/specs/2026-10-15-dynamic-workflows-design.html §08).

Two places, one format: the built-ins shipped in ``builtin_workflows/``,
whose ids are reserved, and the project's, in ``.aegis/workflows/<id>.yaml``
beside the server's ``.aegis.yaml``, so they live in git with the rest of
its configuration. The folders are read on every call: a hand edit or a
``git pull`` is seen at once, and there is no cache to go stale.

A version is the sha256 of the file. Saving needs no approval, because
running a saved workflow always needs one unless a person launched it
(workflows.Launches). Every save and delete appends a line with the text to
``<state>/workflow-saves.jsonl``: the history, where a schedule finds the
version it pinned to show what changed (schedules.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .ops import OpError
from .workflow_lang import parse, walk, with_ids

BUILTIN = Path(__file__).parent / "builtin_workflows"


@dataclass(frozen=True)
class Saved:
    id: str
    text: str
    sha: str
    source: str  # builtin | project
    path: Path
    error: str = ""  # why the file cannot be read as a workflow at all


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Store:
    def __init__(self, config_root: Path, state_root: Path) -> None:
        self.dir = config_root / ".aegis" / "workflows"
        self.history = state_root / "workflow-saves.jsonl"

    def all(self) -> dict[str, Saved]:
        out: dict[str, Saved] = {}
        for source, d in (("builtin", BUILTIN), ("project", self.dir)):
            for p in sorted(d.glob("*.yaml")) if d.is_dir() else []:
                if p.stem in out or not p.is_file():
                    continue  # a built-in's id is reserved; a folder is no file
                try:
                    raw = p.read_bytes()
                except OSError:
                    continue  # gone since the glob, or unreadable
                try:
                    text = raw.decode()
                except UnicodeDecodeError as e:
                    # One bad file is a row that says so, never a menu that fails.
                    why = f"not UTF-8 text: {e.reason} at byte {e.start}"
                    sha = hashlib.sha256(raw).hexdigest()
                    out[p.stem] = Saved(p.stem, "", sha, source, p, why)
                    continue
                out[p.stem] = Saved(p.stem, text, sha_of(text), source, p)
        return out

    def get(self, wid: str) -> Saved:
        s = self.all().get(wid)
        if s is None:
            raise OpError(
                "no_workflow", f"no saved workflow {wid!r}; /workflow alone lists them"
            )
        return s

    def save(self, text: str, agents: set[str], by: dict) -> Saved:
        wf, errors = parse(text, agents)
        if wf is None:
            raise OpError("lint", "\n".join(f"{e.path}: {e.message}" for e in errors))
        if (BUILTIN / f"{wf.id}.yaml").exists():
            raise OpError(
                "reserved", f"{wf.id} is a built-in workflow's id; give yours another"
            )
        written = with_ids(text, wf)
        again, _ = parse(written, agents)
        if again is None or {n.key for n in walk(again.root)} != {
            n.key for n in walk(wf.root)
        }:
            written = text  # the ids would change what it means: keep it as given
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{wf.id}.yaml"
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(written)
        os.replace(tmp, path)
        saved = Saved(wf.id, written, sha_of(written), "project", path)
        self._note(
            {"ev": "saved", "id": wf.id, "sha": saved.sha, "by": by, "text": written}
        )
        return saved

    def delete(self, wid: str, by: dict) -> None:
        s = self.get(wid)
        if s.source == "builtin":
            raise OpError("reserved", f"{wid} is built in; it cannot be deleted")
        s.path.unlink()
        self._note({"ev": "deleted", "id": wid, "sha": s.sha, "by": by})

    def _note(self, rec: dict) -> None:
        self.history.parent.mkdir(parents=True, exist_ok=True)
        with self.history.open("a") as f:
            f.write(json.dumps({"t": time.time(), **rec}) + "\n")

    def _lines(self) -> list[dict]:
        """The history's records; a torn or foreign line is skipped, so a
        crash mid-append costs that one line."""
        if not self.history.exists():
            return []
        out = []
        for x in self.history.read_text(errors="replace").splitlines():
            try:
                r = json.loads(x)
            except ValueError:
                continue
            if isinstance(r, dict) and isinstance(r.get("id"), str) and "ev" in r:
                out.append(r)
        return out

    def last_save(self, wid: str) -> dict | None:
        """The save that wrote the current version, or None when the file
        was written some other way (by hand, by git)."""
        cur = self.all().get(wid)
        for r in reversed(self._lines()):
            if r["id"] == wid and r["ev"] == "saved" and cur and r["sha"] == cur.sha:
                return r
        return None

    def text_of(self, wid: str, sha: str) -> str | None:
        cur = self.all().get(wid)
        if cur is not None and cur.sha == sha:
            return cur.text
        for r in reversed(self._lines()):
            if r["id"] == wid and r.get("sha") == sha and "text" in r:
                return r["text"]
        return None

    def listing(self, agents: set[str]) -> list[dict]:
        rows = []
        for wid, s in sorted(self.all().items()):
            wf, error = _named(s, agents)
            row = {"id": wid, "source": s.source, "sha": s.sha}
            if error:
                row["error"] = error
            else:
                row |= {"title": wf.title, "say": wf.say, "args": wf.args}
            if (last := self.last_save(wid)) is not None:
                row |= {"saved_by": last["by"], "saved_at": last["t"]}
            rows.append(row)
        return rows


def _named(s: Saved, agents: set[str]):
    """The parsed workflow, or what is wrong with the file: it does not
    lint, or its id is not its file's name."""
    if s.error:
        return None, s.error
    wf, errors = parse(s.text, agents)
    if wf is None:
        return None, f"{errors[0].path}: {errors[0].message}"
    if wf.id != s.id:
        return None, f"its id is {wf.id}, but its file is {s.id}.yaml"
    return wf, ""


def check_named(s: Saved, agents: set[str]) -> None:
    """Refuse to arm or launch a saved file the listing marks broken."""
    if error := _named(s, agents)[1]:
        raise OpError("lint", error)
