"""memory-system plugin: persistent memory + periodic dreaming.

Entries live as markdown files with YAML frontmatter under
<project>/.aegis/memory/entries/, indexed by .aegis/memory/MEMORY.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml


VALID_TYPES = ("user", "feedback", "fact", "reference")
MEMORY_SUBDIR = ".aegis/memory"
ENTRIES_SUBDIR = ".aegis/memory/entries"
DREAMS_SUBDIR = ".aegis/memory/dreams"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _kebab(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


@dataclass(frozen=True)
class Entry:
    slug:        str
    type:        str
    name:        str
    description: str
    created:     str
    updated:     str
    content:     str


def _entry_path(root: Path, slug: str) -> Path:
    return root / ENTRIES_SUBDIR / f"{slug}.md"


def _slug_for(type_: str, name: str) -> str:
    return f"{type_}_{_kebab(name)}"


def write_entry(root: Path, type_: str, name: str,
                description: str, content: str) -> Path:
    """Write a new entry. Fails if the slug already exists."""
    if type_ not in VALID_TYPES:
        raise ValueError(f"invalid type {type_!r}; expected one of {VALID_TYPES}")
    slug = _slug_for(type_, name)
    path = _entry_path(root, slug)
    if path.exists():
        raise FileExistsError(f"entry {slug!r} already exists at {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    front = {
        "type": type_,
        "name": _kebab(name),
        "description": description,
        "created": now,
        "updated": now,
    }
    body = (
        "---\n"
        + yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + content.rstrip()
        + "\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def read_entry(root: Path, slug: str) -> Entry:
    """Read a single entry. Raises FileNotFoundError if missing,
    ValueError if frontmatter is malformed."""
    path = _entry_path(root, slug)
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"{path}: malformed frontmatter")
    front = yaml.safe_load(text[4:end]) or {}
    content = text[end + len("\n---\n"):].lstrip()
    return Entry(
        slug=slug,
        type=str(front.get("type", "")),
        name=str(front.get("name", "")),
        description=str(front.get("description", "")),
        created=str(front.get("created", "")),
        updated=str(front.get("updated", "")),
        content=content,
    )


def list_entries(root: Path) -> list[Entry]:
    """Return every well-formed entry under entries/, sorted by name."""
    folder = root / ENTRIES_SUBDIR
    if not folder.exists():
        return []
    out: list[Entry] = []
    for path in sorted(folder.glob("*.md")):
        slug = path.stem
        try:
            out.append(read_entry(root, slug))
        except (ValueError, FileNotFoundError):
            continue
    return out


def rebuild_index(root: Path) -> Path:
    """Rebuild MEMORY.md from the current state of entries/."""
    entries = list_entries(root)
    lines = ["# Memory index", "", "## Index", ""]
    for e in entries:
        lines.append(f"- [{e.name}](entries/{e.slug}.md) — {e.description}")
    path = root / MEMORY_SUBDIR / "MEMORY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


from aegis.tools import tool


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "of", "to",
    "for", "in", "on", "at", "by", "with", "is", "are", "was", "were",
    "be", "been", "being", "this", "that", "these", "those", "i", "you",
    "he", "she", "it", "we", "they", "what", "which", "who", "when",
    "where", "why", "how", "do", "does", "did", "have", "has", "had",
})


def _tokenize(s: str) -> set[str]:
    out: set[str] = set()
    for tok in re.split(r"[^a-z0-9]+", s.lower()):
        if not tok or tok in _STOPWORDS:
            continue
        out.add(tok)
        if len(tok) > 3 and tok.endswith("s"):
            out.add(tok[:-1])
        if len(tok) > 4 and tok.endswith("ing"):
            out.add(tok[:-3])
    return out


def _score(entry: Entry, query_toks: set[str]) -> int:
    name_desc = _tokenize(entry.name + " " + entry.description)
    body = _tokenize(entry.content)
    return (len(query_toks & name_desc) * 2) + len(query_toks & body)


def _snippet(content: str, query_toks: set[str], window: int = 200) -> str:
    low = content.lower()
    for tok in query_toks:
        idx = low.find(tok)
        if idx >= 0:
            half = window // 2
            start = max(0, idx - half)
            end = min(len(content), idx + half)
            return content[start:end].strip()
    return content[:window].strip()


def _project_root() -> Path:
    return Path.cwd()


@tool(timeout=5.0)
async def memory_read(slug: str) -> dict:
    """Fetch one memory entry's full body by slug.

    Args:
        slug: the entry's slug (e.g. "feedback_phrasing").

    Returns:
        a dict with keys: slug, name, type, description, content.
    """
    e = read_entry(_project_root(), slug)
    return {
        "slug": e.slug, "name": e.name, "type": e.type,
        "description": e.description, "content": e.content,
    }


@tool(timeout=5.0)
async def memory_search(query: str, limit: int = 10) -> list[dict]:
    """Keyword search over memory entries.

    Args:
        query: free-text query.
        limit: max results, default 10.

    Returns:
        list of {slug, name, description, score, snippet}, score-descending.
    """
    qtoks = _tokenize(query)
    if not qtoks:
        return []
    scored: list[tuple[int, Entry]] = []
    for e in list_entries(_project_root()):
        s = _score(e, qtoks)
        if s > 0:
            scored.append((s, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict] = []
    for s, e in scored[:limit]:
        out.append({
            "slug": e.slug, "name": e.name,
            "description": e.description, "score": s,
            "snippet": _snippet(e.content, qtoks),
        })
    return out


@tool(timeout=5.0)
async def memory_add(type: str, name: str,
                     description: str, content: str) -> dict:
    """Save a new memory entry.

    Args:
        type: one of "user", "feedback", "fact", "reference".
        name: short label (will be kebab-cased).
        description: one-line summary (used by future sessions to decide
            relevance).
        content: the entry body, markdown.

    Returns:
        {"slug": str, "path": str}.
    """
    root = _project_root()
    path = write_entry(root, type, name, description, content)
    rebuild_index(root)
    return {"slug": path.stem, "path": str(path)}


@tool(timeout=5.0)
async def memory_replace(slug: str, *, description: str | None = None,
                         content: str | None = None) -> dict:
    """Update an existing entry. Name and type are immutable.

    Args:
        slug: the entry's slug.
        description: new description (optional).
        content: new body (optional).

    Returns:
        {"slug": str, "path": str}.
    """
    root = _project_root()
    e = read_entry(root, slug)
    new_desc = description if description is not None else e.description
    new_content = content if content is not None else e.content
    path = _entry_path(root, slug)
    front = {
        "type": e.type, "name": e.name,
        "description": new_desc, "created": e.created,
        "updated": _now_iso(),
    }
    body = (
        "---\n"
        + yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + new_content.rstrip()
        + "\n"
    )
    path.write_text(body, encoding="utf-8")
    if description is not None and description != e.description:
        rebuild_index(root)
    return {"slug": slug, "path": str(path)}


@tool(timeout=5.0)
async def memory_remove(slug: str) -> dict:
    """Delete an entry permanently."""
    root = _project_root()
    path = _entry_path(root, slug)
    if not path.exists():
        raise FileNotFoundError(f"entry {slug!r} not found at {path}")
    path.unlink()
    rebuild_index(root)
    return {"slug": slug, "removed": True}


from aegis.hooks import hook, PreTurnContext, PreTurnResult


PRIMER = """\
# Memory

You have a persistent memory at .aegis/memory/. The MEMORY.md index above
lists everything you know. Use memory_search(query) to find an entry's
body, or memory_read(slug) when you already know the slug.

Write a memory when:
- the user corrects you ("don't", "stop X") -> save as `feedback`
- the user reveals a preference, role, or constraint -> `user`
- you discover a non-obvious fact about the project or tooling -> `fact`
- the user names an external system you'll need again -> `reference`

Skip trivial / easily-rediscovered things. When unsure, save -- the
dream pass will consolidate later.

Tools:
- memory_search(query)         -- find entries by keyword
- memory_read(slug)            -- fetch one entry's body
- memory_add(type, name, ...)  -- save a new memory
- memory_replace(slug, ...)    -- update an existing one
- memory_remove(slug)          -- delete (use sparingly outside dream pass)
"""


TURN_ZERO_CAP_TOKENS = 4000
TURN_N_CAP_WORDS = 1000
TOP_K = 5


def _approx_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)


def _read_file_or_none(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _build_turn_zero(project_root: Path) -> str:
    mem_dir = project_root / MEMORY_SUBDIR
    soul = _read_file_or_none(mem_dir / "SOUL.md")
    user = _read_file_or_none(mem_dir / "USER.md")
    entries = list_entries(project_root)
    entries.sort(
        key=lambda e: (mem_dir / "entries" / f"{e.slug}.md").stat().st_mtime,
        reverse=True,
    )

    def _assemble(included: list[Entry], dropped: int) -> str:
        parts: list[str] = []
        if soul:
            parts.append(soul)
        if user:
            parts.append(user)
        if included:
            parts.append("## Memory index\n")
            for e in included:
                parts.append(f"- [{e.name}](entries/{e.slug}.md) -- {e.description}")
        if dropped > 0:
            parts.append(
                f"... {dropped} more entries; use memory_search to find specific ones"
            )
        parts.append("")
        parts.append(PRIMER)
        return "\n".join(parts)

    text = _assemble(entries, 0)
    total = len(entries)
    while _approx_tokens(text) > TURN_ZERO_CAP_TOKENS and entries:
        entries.pop()
        text = _assemble(entries, total - len(entries))
    return text


def _build_turn_n(project_root: Path, user_message: str) -> str | None:
    qtoks = _tokenize(user_message)
    if not qtoks:
        return None
    now_ts = datetime.now(timezone.utc).timestamp()
    scored: list[tuple[int, Entry]] = []
    for e in list_entries(project_root):
        s = _score(e, qtoks)
        try:
            mtime = (project_root / ENTRIES_SUBDIR / f"{e.slug}.md").stat().st_mtime
            if now_ts - mtime < 86400:
                s += 1
        except OSError:
            pass
        if s >= 2:
            scored.append((s, e))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    lines = ["## Possibly relevant memory", ""]
    used = sum(len(line.split()) for line in lines)
    for _, e in scored[:TOP_K]:
        line = f"- **{e.name}** -- {e.description}"
        if used + len(line.split()) > TURN_N_CAP_WORDS:
            break
        lines.append(line)
        used += len(line.split())
    if len(lines) == 2:
        return None
    return "\n".join(lines)


@hook("pre_turn")
async def inject_memory(ctx: PreTurnContext) -> PreTurnResult | None:
    """Inject SOUL+USER+index+primer on turn 0, top-K teasers afterward."""
    if not ctx.history:
        return PreTurnResult(prepend_system=_build_turn_zero(ctx.project_root))
    body = _build_turn_n(ctx.project_root, ctx.user_message)
    if body is None:
        return None
    return PreTurnResult(prepend_system=body)


@hook("session_start")
async def log_session_open(ctx) -> None:
    """Observer: best-effort note that a session has started."""
    pass


# --- dream workflow ---------------------------------------------------

import json as _json
import time as _time

from aegis.workflow import workflow


def _recent_session_files(project_root: Path, lookback_days: int,
                          max_files: int) -> list[Path]:
    sessions = project_root / ".aegis" / "state" / "sessions"
    if not sessions.exists():
        return []
    cutoff = _time.time() - lookback_days * 86400
    out: list[Path] = []
    for p in sessions.glob("*.jsonl"):
        try:
            if p.stat().st_mtime >= cutoff:
                out.append(p)
        except OSError:
            continue
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out[:max_files]


def _stage1_prompt(transcript_path: Path) -> str:
    transcript = transcript_path.read_text(encoding="utf-8")
    return (
        "Here is one aegis session transcript. Summarize what happened, "
        "propose memory entries the agent should have saved but didn't, "
        "and note observations -- surprising patterns, contradictions, "
        "repeated stumbles. Return JSON only, matching this shape:\n"
        '{"session_handle": "...", "summary": "...", '
        '"proposed_entries": [...], "observations": [...]}\n\n'
        f"Session handle: {transcript_path.stem}\n\n"
        "Transcript:\n"
        f"{transcript}\n"
    )


def _stage2_prompt(current_entries: list[Entry],
                   proposals: list[dict]) -> str:
    body = []
    for e in current_entries:
        body.append(f"=== {e.slug} ({e.type}) -- {e.description} ===\n{e.content}")
    return (
        "Consolidate. Given the current memory entries and the proposals "
        "from each session reader, emit a JSON action plan:\n"
        '{"actions": [{"action": "add", "type": "...", "name": "...", '
        '"description": "...", "content": "..."}, '
        '{"action": "replace", "slug": "...", "description": "...", '
        '"content": "..."}, '
        '{"action": "remove", "slug": "..."}], "rationale": "..."}\n\n'
        "Current entries:\n\n" + "\n\n".join(body) +
        "\n\nProposals:\n" + _json.dumps(proposals, indent=2)
    )


def _stage3_prompt(observations: list[str], rationale: str) -> str:
    return (
        "Write a short narrative dream log (500-1000 words) in first person "
        "from the agent's perspective. Reflect on patterns from the "
        "observations and the consolidation rationale. Prose only.\n\n"
        "Observations:\n" + "\n".join(f"- {o}" for o in observations) +
        f"\n\nConsolidation rationale: {rationale}\n"
    )


async def _apply_actions(root: Path, actions: list[dict]) -> list[str]:
    """Apply consolidation actions via the write helpers."""
    touched: list[str] = []
    for a in actions:
        op = a.get("action")
        try:
            if op == "add":
                p = write_entry(root, a["type"], a["name"],
                                a["description"], a["content"])
                touched.append(p.stem)
            elif op == "replace":
                slug = a["slug"]
                e = read_entry(root, slug)
                new_desc = a.get("description", e.description)
                new_content = a.get("content", e.content)
                path = _entry_path(root, slug)
                front = {"type": e.type, "name": e.name,
                         "description": new_desc, "created": e.created,
                         "updated": _now_iso()}
                path.write_text(
                    "---\n" + yaml.safe_dump(front, sort_keys=False,
                                             allow_unicode=True) +
                    "---\n\n" + new_content.rstrip() + "\n",
                    encoding="utf-8")
                touched.append(slug)
            elif op == "remove":
                _entry_path(root, a["slug"]).unlink()
                touched.append(a["slug"])
        except (FileNotFoundError, FileExistsError, KeyError):
            continue
    rebuild_index(root)
    return touched


def _write_dream_log(root: Path, prose: str, *, actions: list[str],
                     sessions: list[str], lookback_days: int) -> Path:
    from datetime import date
    dreams_dir = root / DREAMS_SUBDIR
    dreams_dir.mkdir(parents=True, exist_ok=True)
    path = dreams_dir / f"dream-{date.today().isoformat()}.md"
    front = yaml.safe_dump({
        "actions": actions,
        "sessions_read": sessions,
        "lookback_days": lookback_days,
    }, sort_keys=False)
    path.write_text(
        "---\n" + front + "---\n\n" + prose.rstrip() + "\n",
        encoding="utf-8",
    )
    return path


@workflow
async def dream(engine, *, lookback_days: int = 7,
                max_session_files: int = 50,
                dreamer_queue: str = "dreamer-queue") -> dict:
    """Periodic memory consolidation + synthesis."""
    root = _project_root()
    files = _recent_session_files(root, lookback_days, max_session_files)
    proposals: list[dict] = []
    observations: list[str] = []
    session_handles: list[str] = []

    for p in files:
        try:
            raw = await engine.delegate(dreamer_queue, _stage1_prompt(p))
            parsed = _json.loads(raw)
        except (ValueError, _json.JSONDecodeError):
            continue
        session_handles.append(parsed.get("session_handle", p.stem))
        proposals.extend(parsed.get("proposed_entries", []))
        observations.extend(parsed.get("observations", []))

    current = list_entries(root)
    try:
        raw2 = await engine.delegate(
            dreamer_queue, _stage2_prompt(current, proposals))
        plan = _json.loads(raw2)
    except (ValueError, _json.JSONDecodeError):
        plan = {"actions": [], "rationale": ""}
    touched = await _apply_actions(root, plan.get("actions", []))

    prose = await engine.delegate(
        dreamer_queue, _stage3_prompt(observations, plan.get("rationale", "")))
    log_path = _write_dream_log(
        root, prose, actions=touched,
        sessions=session_handles, lookback_days=lookback_days,
    )
    return {
        "sessions_read": len(files),
        "actions_applied": len(touched),
        "dream_log": str(log_path),
    }
