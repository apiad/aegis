"""Every icon in the client is a symbol in the sprite in js/glyphs.js (#644).

A sprite symbol is drawn in the theme's colours at any size; an emoji brings
its own colours, a Unicode mark renders in whatever font the system falls back
to, and an SVG written inline is a copy that drifts from its twin. So: no
emoji and no icon-like marks in the client, no ``<svg>`` outside the sprite,
every name the code asks for is a symbol, every symbol is asked for, and the
icons help page lists exactly the symbols.

Which names the code asks for is read from its source, so a name must be
written where this test looks: a quoted argument of a call to a function whose
name says icon (``icon``, ``entryIcon``, ``iconButton``, ``withIcon``), the
``icon:`` of a menu item, a value of a table named ``*_ICON``, ``*_GLYPH`` or
``SYMBOL``, ``#g-<name>`` in markup, the Python glyph tables, or a family
whose member is built from a value (``f-<kind>``, ``h-<harness>``,
``e-<effort>``, ``p-<permission>``)."""

import re
from pathlib import Path

from aegis import agents
from aegis.helppages import HELP_DIR

SRC = Path(__file__).parents[1] / "src" / "aegis"
CLIENT = SRC / "client"
JS = CLIENT / "js"
GLYPHS = JS / "glyphs.js"

SYMBOL = re.compile(r'<symbol id="g-([a-z0-9-]+)"')

# Characters that are typography, not icons: arithmetic and flow arrows in
# text, key names in shortcut labels, the separator dot, the ellipsis, and the
# block cursor the phosphor theme blinks after a working row.
TYPOGRAPHY = set("\u2212\u2192\u2190\u2191\u2193\u21b3\u2318\u00b7\u2026\u2588")

# Pictographs, dingbats, geometric shapes, arrows, technical and
# miscellaneous symbols, enclosed alphanumerics, box drawing, and the emoji
# variation selector.
MARK = re.compile(
    "[\u2190-\u21ff\u2300-\u23ff\u2440-\u24ff\u2500-\u25ff\u2600-\u27bf"
    "\u2900-\u2bff\u2022\ufe0f\U0001f000-\U0001faff]"
)

# The families whose member is chosen by a value, and the values.
FAMILIES = {
    "h-": set(agents.HARNESSES),
    "e-": set(agents.EFFORTS),
    "p-": set(agents.PERMISSION_ORDER),
}


def sprite() -> str:
    src = GLYPHS.read_text()
    return src[src.index("const SPRITE") : src.index("</defs>")]


def symbols() -> set[str]:
    return set(SYMBOL.findall(sprite()))


def code_files() -> list[Path]:
    return [
        *JS.glob("*.js"),
        CLIENT / "index.html",
        *(CLIENT / "css").glob("*.css"),
        *(CLIENT / "themes").glob("*.css"),
    ]


BLOCK = re.compile(r"/\*.*?\*/|<!--.*?-->", re.S)


def code_lines():
    """Each line of client code with its comments blanked, as (file:line, text)."""
    for p in code_files():
        text = p.read_text()
        if p == GLYPHS:
            text = text.replace(sprite(), "")
        text = BLOCK.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)
        for n, line in enumerate(text.splitlines(), 1):
            line = re.sub(r"(^|\s)//.*", "", line)
            if line.strip():
                yield f"{p.name}:{n}", line


def python_glyphs() -> set[str]:
    """The names the server sends: every ``*_GLYPH`` constant and the values of
    every ``*GLYPH*`` table in the transcript and the journal."""
    from aegis.journal import render
    from aegis.transcript import describe

    out = set()
    for mod in (describe, render):
        for k, v in vars(mod).items():
            if "GLYPH" not in k:
                continue
            if isinstance(v, str):
                out.add(v)
            elif isinstance(v, dict):
                out.update(x for x in v.values() if isinstance(x, str))
    return out


QUOTED = re.compile(r"""["'`]([a-z0-9-]+)["'`]""")


def call_args(line: str, at: int) -> list[str]:
    """The top-level arguments of the call whose ``(`` is at ``at``, with
    nested calls, subscripts and compared strings (``kind === "dir"``) left
    out."""
    depth, args, cur = 0, [], []
    for c in line[at:]:
        if c in "([{":
            depth += 1
            if depth == 1:
                continue
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                break
        if depth == 1 and c == ",":
            args.append("".join(cur))
            cur = []
        elif depth == 1:
            cur.append(c)
    args.append("".join(cur))
    return [re.sub(r"""[=!]==?\s*["'`][^"'`]*["'`]""", "", x) for x in args]


def asked() -> dict[str, str]:
    """The names the code asks the sprite for, each with one place it does."""
    out = {name: "python" for name in python_glyphs()}
    for at, line in code_lines():
        found = []
        for m in re.finditer(r"\b(\w*[iI]con\w*)\(", line):
            if m.group(1) == "factIcon":
                continue  # factIcon(family, value): FAMILIES checks those.
            args = call_args(line, m.end() - 1)
            # iconText(name, text): the text is not a name.
            for arg in args[:1] if m.group(1) == "iconText" else args:
                found += QUOTED.findall(arg)
        found += re.findall(r"""\bicon:\s*["']([a-z0-9-]+)["']""", line)
        found += re.findall(r"#g-([a-z0-9-]+)", line)
        table = re.search(
            r"\b(?:const|let)\s+\w*(?:_ICON|_GLYPH|SYMBOL)\s*=\s*\{(.*)\}", line
        )
        if table:
            found += re.findall(r""":\s*["']([a-z0-9-]+)["']""", table.group(1))
        for name in found:
            out.setdefault(name, at)
    return out


def test_the_client_draws_no_emoji_or_icon_marks():
    found = [
        f"{at} {ch!r}"
        for at, line in code_lines()
        for ch in MARK.findall(line)
        if ch not in TYPOGRAPHY
    ]
    assert found == []


def test_the_client_writes_no_svg_outside_the_sprite():
    """The exceptions draw no icon: the favicon (ping.js) is a fixed image
    outside the page and its theme, a monitor's sparkline (monitors.js) is a
    chart, and a theme's background pattern is a data URL in its CSS."""
    allowed = {"ping.js", "monitors.js"}
    found = [
        at
        for at, line in code_lines()
        if at.split(":")[0] not in allowed
        and not at.startswith(tuple(p.name for p in (CLIENT / "themes").glob("*.css")))
        and re.search(r"<svg\b|createElementNS\(", line)
        and not re.fullmatch(
            r'(?:[^<]|<(?!svg\b))*(?:<svg [^>]*><use href="#g-[a-z0-9-]+"/></svg>(?:[^<]|<(?!svg\b))*)+',
            line,
        )
        and not (at.startswith("glyphs.js") and "createElementNS(NS" in line)
    ]
    assert found == []


def test_every_name_the_code_asks_for_is_a_symbol():
    have = symbols()
    missing = {name: at for name, at in asked().items() if name not in have}
    assert missing == {}


def test_every_symbol_is_used():
    named = asked()
    editor = (JS / "editor.js").read_text()
    unused = []
    for name in sorted(symbols()):
        if name in named:
            continue
        if name.startswith("f-") and f'"{name[2:]}"' in editor:
            continue
        if any(
            name.startswith(p) and name[len(p) :] in values
            for p, values in FAMILIES.items()
        ):
            continue
        unused.append(name)
    assert unused == []


def test_each_family_has_a_symbol_per_value():
    have = symbols()
    missing = [
        f"{p}{v}"
        for p, values in FAMILIES.items()
        for v in sorted(values)
        if f"{p}{v}" not in have
    ]
    assert missing == []


def test_the_icons_page_lists_exactly_the_symbols():
    """help/icons.md is a table, one row per symbol, its first cell the name
    in backticks; the Help tab draws the symbol beside it in the theme."""
    rows = re.findall(
        r"^\| `g-([a-z0-9-]+)` \|", (HELP_DIR / "icons.md").read_text(), re.M
    )
    assert len(rows) == len(set(rows)), "a symbol listed twice"
    assert sorted(rows) == sorted(symbols())
