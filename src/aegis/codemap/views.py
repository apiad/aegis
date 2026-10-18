"""The views ``aegis map`` prints, each a plain-text block sized for an agent's context.

Paths are relative to the repository root. A line ending in ``?`` is an edge
found by name only; every other edge is sure (see the package docstring).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .index import Index
from .parse import Def

UNSURE = "? = matched by name only, not confirmed by an import"
_KINDS = ("class", "interface", "type", "module", "function", "method")


def _under(rel: str, base: str) -> bool:
    return not base or rel == base or rel.startswith(base + "/")


def repo(ix: Index, path: str, depth: int) -> str:
    base = path.strip("/")
    counts: dict[str, Counter] = defaultdict(Counter)
    docs: dict[str, str] = {}
    for rel, (_, f) in ix.files.items():
        if not _under(rel, base):
            continue
        parts = rel[len(base) :].strip("/").split("/")
        key = "/".join(parts[:depth])
        c = counts[key]
        c["files"] += 1
        for d in f.defs:
            c[d.kind] += 1
        if len(parts) <= depth:
            docs[key] = f.doc
    out = [
        f"# {base or ix.root.name}: {sum(c['files'] for c in counts.values())} files"
    ]
    for key in sorted(counts):
        c = counts[key]
        kinds = " ".join(f"{c[k]} {k}" for k in _KINDS if c[k])
        if key in docs:
            out.append(f"{key}  {kinds}" + (f"  — {docs[key]}" if docs[key] else ""))
        else:
            out.append(f"{key}/  {c['files']} files  {kinds}")
    return "\n".join(out)


def deps(ix: Index, path: str) -> str:
    base = path.strip("/")
    edges: Counter = Counter()
    for rel, (_, f) in ix.files.items():
        if not _under(rel, base):
            continue
        for name, *_ in f.refs:
            cands, sure = ix.resolve(name, rel)
            if sure and cands[0].file != rel:
                edges[(rel, cands[0].file)] += 1
    by_src: dict[str, list[str]] = defaultdict(list)
    for (a, b), n in edges.most_common():
        by_src[a].append(f"{b}({n})")
    lines = [
        f"{a} → {', '.join(bs[:12])}{' …' if len(bs) > 12 else ''}"
        for a, bs in sorted(by_src.items())
    ]
    return "\n".join(lines) or f"no sure dependencies under {base or 'the repository'}"


def outline(ix: Index, path: str) -> str:
    out = []
    for rel in sorted(r for r in ix.files if _under(r, path.strip("/"))):
        f = ix.info(rel)
        out.append(f"## {rel}" + (f"  — {f.doc}" if f.doc else ""))
        level: dict[str, int] = {}
        for d in f.defs:
            level[d.id] = level[d.parent] + 1 if d.parent in level else 0
            doc = f"  — {d.doc}" if d.doc else ""
            out.append(f"{'  ' * level[d.id]}{d.line}: {d.signature}{doc}")
    return "\n".join(out) or f"nothing indexed under {path}"


def find(ix: Index, name: str) -> str:
    return (
        "\n".join(
            f"{d.id}  {d.kind}, lines {d.line}-{d.end_line}" for d in ix.symbol(name)
        )
        or f"{name}: not found"
    )


def show(ix: Index, symbol: str) -> str:
    out = []
    for d in ix.symbol(symbol):
        src = (ix.root / d.file).read_bytes()[d.start : d.end].decode(errors="replace")
        out.append(f"## {d.id}  lines {d.line}-{d.end_line}\n{src}")
    return "\n\n".join(out) or f"{symbol}: not found"


def callers(ix: Index, symbol: str, depth: int, calls_only: bool = True) -> str:
    """Who uses symbol, level by level, grouped by file. Level 2 and beyond
    expand only through sure edges, since a name match there multiplies noise."""
    out = []
    unsure = False
    for target in ix.symbol(symbol):
        out.append(f"# {'callers' if calls_only else 'impact'} of {target.id}")
        seen = {target.id}
        frontier: list[Def] = [target]
        for level in range(1, depth + 1):
            nxt: list[Def] = []
            by_file: dict[str, list[str]] = defaultdict(list)
            for t in frontier:
                for u in ix.uses(t, calls_only):
                    if u.where in seen:
                        continue
                    seen.add(u.where)
                    name = (
                        u.where.split(":", 1)[1] if u.where in ix.defs else "<module>"
                    )
                    by_file[u.file].append(f"{name}:{u.line}{'' if u.sure else ' ?'}")
                    unsure |= not u.sure
                    if u.sure and u.where in ix.defs:
                        nxt.append(ix.defs[u.where])
            for rel in sorted(by_file):
                out.append(
                    f"{'  ' * (level - 1)}L{level} {rel}: {', '.join(by_file[rel])}"
                )
            frontier = nxt
            if not frontier:
                break
        files = {w.split(":", 1)[0] for w in seen if w != target.id}
        out.append(f"= {len(seen) - 1} uses in {len(files)} files")
    if unsure:
        out.append(UNSURE)
    return "\n".join(out) or f"{symbol}: not found"


def callees(ix: Index, symbol: str) -> str:
    out = []
    unsure = False
    for d in ix.symbol(symbol):
        out.append(f"# callees of {d.id}")
        seen = set()
        for line, cands, sure in ix.callees(d):
            key = tuple(c.id for c in cands)
            if key in seen:
                continue
            seen.add(key)
            unsure |= not sure
            more = (
                f" ? ({len(cands)} candidates)"
                if len(cands) > 1
                else ""
                if sure
                else " ?"
            )
            out.append(f"{line}: {cands[0].id}{more}")
    if unsure:
        out.append(UNSURE)
    return "\n".join(out) or f"{symbol}: not found"
