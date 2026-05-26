from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token

_MD = (MarkdownIt("commonmark", {"breaks": False, "html": False})
       .enable("strikethrough")
       .enable("table"))
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
        elif t.type == "image":
            alt = t.content or ""
            parts.append(f"[image: {_esc_text(alt)}]")
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


def _walk_tokens(tokens: list[Token], md_source: str) -> str:
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t.type == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < n else None
            if inline and inline.type == "inline":
                out.append(_render_inline(inline.children or []))
                out.append("\n\n")
            i += 3
            continue
        if t.type == "heading_open":
            inline = tokens[i + 1] if i + 1 < n else None
            out.append("<b>")
            if inline and inline.type == "inline":
                out.append(_render_inline(inline.children or []))
            out.append("</b>\n\n")
            i += 3
            continue
        if t.type == "fence":
            lang = (t.info or "").strip().split(maxsplit=1)
            lang_s = lang[0] if lang else ""
            body = _esc_text(t.content).replace("'", "&#x27;").rstrip("\n") + "\n"
            if lang_s:
                out.append(f'<pre><code class="language-{_esc_attr(lang_s)}">{body}</code></pre>')
            else:
                out.append(f"<pre><code>{body}</code></pre>")
            out.append("\n\n")
            i += 1
            continue
        if t.type == "code_block":
            body = _esc_text(t.content).replace("'", "&#x27;").rstrip("\n") + "\n"
            out.append(f"<pre><code>{body}</code></pre>")
            out.append("\n\n")
            i += 1
            continue
        if t.type == "blockquote_open":
            depth = 1
            j = i + 1
            inner: list[Token] = []
            while j < n and depth > 0:
                if tokens[j].type == "blockquote_open":
                    depth += 1
                elif tokens[j].type == "blockquote_close":
                    depth -= 1
                    if depth == 0:
                        break
                inner.append(tokens[j])
                j += 1
            inside = _walk_tokens(inner, md_source).rstrip("\n")
            out.append(f"<blockquote>{inside}</blockquote>\n\n")
            i = j + 1
            continue
        if t.type == "table_open":
            src_start = t.map[0] if t.map else None
            src_end = t.map[1] if t.map else None
            j = i
            while j < n and tokens[j].type != "table_close":
                j += 1
            if src_start is not None and src_end is not None:
                src = "\n".join(md_source.splitlines()[src_start:src_end])
                out.append(f"<pre>{_esc_text(src)}</pre>\n\n")
            i = j + 1
            continue
        if t.type == "bullet_list_open" or t.type == "ordered_list_open":
            ordered = t.type == "ordered_list_open"
            close_type = "ordered_list_close" if ordered else "bullet_list_close"
            j = i + 1
            num = 1
            while j < n and tokens[j].type != close_type:
                if tokens[j].type == "list_item_open":
                    depth = 1
                    k = j + 1
                    item_inline = ""
                    while k < n and depth > 0:
                        if tokens[k].type == "list_item_open":
                            depth += 1
                        elif tokens[k].type == "list_item_close":
                            depth -= 1
                            if depth == 0:
                                break
                        elif tokens[k].type == "inline":
                            item_inline += _render_inline(tokens[k].children or [])
                        k += 1
                    prefix = f"{num}. " if ordered else "• "
                    out.append(f"{prefix}{item_inline}\n")
                    num += 1
                    j = k + 1
                else:
                    j += 1
            out.append("\n")
            i = j + 1
            continue
        if t.type == "hr":
            out.append("───────\n\n")
            i += 1
            continue
        if t.type == "inline" and t.children:
            out.append(_render_inline(t.children))
            i += 1
            continue
        i += 1
    return "".join(out)


def render(md: str) -> str:
    tokens = _MD.parse(md)
    return _walk_tokens(tokens, md).rstrip()
