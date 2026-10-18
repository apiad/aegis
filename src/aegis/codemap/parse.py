"""One source file into its definitions and the names it uses.

A definition is a class, function or method with its span, signature and first
doc line. A reference is every identifier that is not a definition's name or a
binding (a parameter, an assignment target), with the definition it sits in.
Grammars come from tree-sitter-language-pack, which downloads one the first
time a language is parsed; a grammar that cannot be had raises, and the index
skips that file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Extensions to the pack's language names. Languages the pack has no tags
# query for still index, through generic_defs.
EXT = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".sh": "bash",
    ".hs": "haskell",
    ".zig": "zig",
    ".dart": "dart",
    ".jl": "julia",
    ".ml": "ocaml",
    ".clj": "clojure",
}
# Languages whose names resolve against each other: a .ts file imports .js.
FAMILY = {"typescript": "js", "tsx": "js", "javascript": "js", "c": "c", "cpp": "c"}
# Languages where a method calls a sibling without self./this.
IMPLICIT_THIS = {"java", "csharp", "kotlin", "swift", "scala", "cpp", "ruby", "php"}

_DEF_TYPES = re.compile(
    r"(function|method|class|struct|interface|trait|enum|impl|module|constructor)"
    r"_?(definition|declaration|item|specifier)?$"
)
_CONTAINER = re.compile("class|struct|interface|trait|enum|impl|module")
_SKIP_KINDS = {"constant", "property"}
_IMPORT_LINE = re.compile(r"\s*(import|from|use|require|#include|using)\b|.*require\(")


@dataclass(slots=True)
class Def:
    id: str  # path:Qualified.name
    name: str
    kind: str
    file: str
    start: int
    end: int
    line: int
    end_line: int
    signature: str
    doc: str
    parent: str | None = None


# A reference: (name, line, is_call, enclosing Def.id or None at module level,
# after a `.`, `->` or `::`). A tuple, not a class: an index holds hundreds of
# thousands, and tuples unpickle in a third of the time.
Ref = tuple[str, int, bool, str | None, bool]


@dataclass
class FileInfo:
    lang: str
    doc: str
    defs: list[Def] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)
    imports: str = ""  # the import lines of the file's head, as text


_queries: dict = {}


def _query(lang: str):
    import tree_sitter as ts
    import tree_sitter_language_pack as pack

    if lang not in _queries:
        src = pack.get_tags_query(lang) or ""
        if lang in ("typescript", "tsx"):
            # The TypeScript query covers only TS syntax and relies on
            # inheriting JavaScript's: alone it found 225 defs in Excalidraw,
            # combined 3,965.
            src = (pack.get_tags_query("javascript") or "") + "\n" + src
        _queries[lang] = ts.Query(pack.get_language(lang), src) if src.strip() else None
    return _queries[lang]


def first_line(s: str) -> str:
    # Drop the string's own delimiters, not a docstring's leading ``code``.
    s = re.sub(r"^[rRbBuUfF]*(\"\"\"|'''|[\"'`])|(\"\"\"|'''|[\"'`])$", "", s.strip())
    for line in s.splitlines():
        line = re.sub(r"^[\s/*#!-]+|[\s*/]+$", "", line).strip("\"' ")
        if line:
            return line[:140]
    return ""


def _py_doc(node) -> str:
    body = node if node.type == "module" else node.child_by_field_name("body")
    if body is None:
        return ""
    for c in body.named_children:
        if c.type == "comment":
            continue
        if c.type == "expression_statement" and c.named_children:
            c = c.named_children[0]
        return first_line(c.text.decode(errors="replace")) if c.type == "string" else ""
    return ""


def _comment_doc(node) -> str:
    lines: list[str] = []
    prev = node.prev_named_sibling
    while (
        prev is not None
        and "comment" in prev.type
        and node.start_point[0] - prev.end_point[0] <= 1 + len(lines)
    ):
        lines.insert(0, prev.text.decode(errors="replace"))
        prev = prev.prev_named_sibling
    return first_line("\n".join(lines))


def _file_doc(root, lang: str) -> str:
    if lang == "python":
        return _py_doc(root)
    kids = root.named_children
    if kids and "comment" in kids[0].type:
        return first_line(kids[0].text.decode(errors="replace"))
    return ""


def _signature(node, src: bytes) -> str:
    body = node.child_by_field_name("body")
    end = (
        body.start_byte
        if body is not None
        else min(node.end_byte, node.start_byte + 200)
    )
    s = " ".join(src[node.start_byte : end].decode(errors="replace").split())
    return s.rstrip(" {:")[:160]


def _generic_defs(root):
    """For a language without a tags query: any node whose type reads like a definition."""
    stack = [root]
    while stack:
        n = stack.pop()
        if _DEF_TYPES.search(n.type):
            name = n.child_by_field_name("name")
            if name is not None:
                yield n, name, "class" if _CONTAINER.search(n.type) else "function"
        stack.extend(reversed(n.children))


def _is_binding(n) -> bool:
    par = n.parent
    if par is None:
        return False
    return (
        "parameter" in par.type
        or "keyword_argument" in par.type
        or any(par.child_by_field_name(f) == n for f in ("name", "key", "pattern"))
        or ("assignment" in par.type and par.child_by_field_name("left") == n)
    )


def parse_file(rel: str, src: bytes, lang: str) -> FileInfo:
    import tree_sitter as ts
    import tree_sitter_language_pack as pack

    root = pack.get_parser(lang).parse(src).root_node
    info = FileInfo(lang=lang, doc=_file_doc(root, lang))
    found = []  # (node, name node, kind)
    call_starts: set[int] = set()
    q = _query(lang)
    if q is None:
        found = list(_generic_defs(root))
    else:
        for _, caps in ts.QueryCursor(q).matches(root):
            names = caps.get("name") or []
            if not names:
                continue
            for cap, nodes in caps.items():
                if cap.startswith("definition."):
                    kind = cap.split(".", 1)[1]
                    if kind not in _SKIP_KINDS:
                        found.append((nodes[0], names[0], kind))
                elif cap.startswith("reference."):
                    call_starts.add(names[0].start_byte)
    found.sort(key=lambda f: (f[0].start_byte, -f[0].end_byte))

    by_span: dict[tuple[int, int], Def] = {}
    open_defs: list[Def] = []
    for node, name_node, kind in found:
        while open_defs and open_defs[-1].end <= node.start_byte:
            open_defs.pop()
        parent = open_defs[-1] if open_defs else None
        name = name_node.text.decode(errors="replace")
        if (
            kind == "function"
            and parent is not None
            and parent.kind in ("class", "interface", "module")
        ):
            kind = "method"
        qual = f"{parent.id.split(':', 1)[1]}.{name}" if parent else name
        d = Def(
            id=f"{rel}:{qual}",
            name=name,
            kind=kind,
            file=rel,
            start=node.start_byte,
            end=node.end_byte,
            line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=_signature(node, src),
            doc=_py_doc(node) if lang == "python" else _comment_doc(node),
            parent=parent.id if parent else None,
        )
        info.defs.append(d)
        open_defs.append(d)
        by_span.setdefault((node.start_byte, node.end_byte), d)

    def_names = {f[1].start_byte for f in found}
    walk = [(root, None)]
    while walk:
        n, enc = walk.pop()
        d = by_span.get((n.start_byte, n.end_byte))
        if d is not None and n.child_count:
            enc = d.id
        if n.child_count == 0:
            if (
                (
                    n.type.endswith("identifier")
                    or n.type in ("constant", "type_identifier")
                )
                and n.start_byte not in def_names
                and not _is_binding(n)
            ):
                prev = n.prev_sibling
                info.refs.append(
                    (
                        src[n.start_byte : n.end_byte].decode(errors="replace"),
                        n.start_point[0] + 1,
                        n.start_byte in call_starts,
                        enc,
                        prev is not None and prev.type in (".", "->", "::", "?."),
                    )
                )
            continue
        walk.extend((c, enc) for c in reversed(n.children))

    head = src[:4000].decode(errors="replace")
    info.imports = "\n".join(
        line for line in head.splitlines() if _IMPORT_LINE.match(line)
    )
    return info
