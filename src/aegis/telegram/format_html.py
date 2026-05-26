from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token

_MD = MarkdownIt("commonmark", {"breaks": False, "html": False}).enable("strikethrough")
# Disable URL percent-encoding — we HTML-escape attrs ourselves; double-encoding
# would mangle the user-visible href.
_MD.normalizeLink = lambda url: url
_MD.validateLink = lambda url: True


def _esc_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    return _esc_text(s).replace('"', "&quot;")


_INLINE_TAGS: dict[str, tuple[str, str]] = {
    "strong": ("<b>", "</b>"),
    "em": ("<i>", "</i>"),
    "s": ("<s>", "</s>"),
}


def _attr_get(t: Token, name: str) -> str:
    attrs = t.attrs
    if attrs is None:
        return ""
    if hasattr(attrs, "items"):
        for k, v in attrs.items():
            if k == name:
                return v
        return ""
    for pair in attrs:
        if pair[0] == name:
            return pair[1]
    return ""


def _render_inline(tokens: list[Token]) -> str:
    parts: list[str] = []
    for t in tokens:
        if t.type == "text":
            parts.append(_esc_text(t.content))
        elif t.type == "code_inline":
            parts.append(f"<code>{_esc_text(t.content)}</code>")
        elif t.type == "softbreak" or t.type == "hardbreak":
            parts.append("\n")
        elif t.type.endswith("_open"):
            tag = t.tag
            if tag in _INLINE_TAGS:
                parts.append(_INLINE_TAGS[tag][0])
            elif tag == "a":
                href = _attr_get(t, "href")
                parts.append(f'<a href="{_esc_attr(href)}">')
        elif t.type.endswith("_close"):
            tag = t.tag
            if tag in _INLINE_TAGS:
                parts.append(_INLINE_TAGS[tag][1])
            elif tag == "a":
                parts.append("</a>")
        elif t.type == "inline" and t.children:
            parts.append(_render_inline(t.children))
    return "".join(parts)


def render(md: str) -> str:
    tokens = _MD.parse(md)
    out: list[str] = []
    for t in tokens:
        if t.type == "inline" and t.children:
            out.append(_render_inline(t.children))
        elif t.type == "paragraph_open":
            pass
        elif t.type == "paragraph_close":
            out.append("\n\n")
    return "".join(out).rstrip()
