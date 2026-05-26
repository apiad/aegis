# Aegis Telegram v0.11 — Renderer + Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Telegram MarkdownV2-escape-everything render path with HTML parse mode + greedy chunker + `sendDocument` overflow; convert the status-line refresher into an event-driven per-turn ticker; migrate observers off the clobber-prone primary slot; ship the seven D-bucket correctness fixes; release `v0.11.0`.

**Architecture:** `format.py` replaces `escape_md`/`chunk` with HTML render + greedy `chunk()` returning `list[str] | Spillover`. New `format_html.py` walks `markdown-it-py` tokens into Telegram's HTML subset. `bot.py` gains `parse_mode: str | None` on `send_message`/`edit_message` and a new `send_document` (multipart POST). `frontend.py` migrates to per-handle state dicts, registers via `add_event_observer`/`add_state_observer`/`add_close_observer` (new), and edits the ticker on tool-use boundaries instead of a 2s timer. `session.py` gains `on_close` + `add_close_observer` matching the existing observer patterns. `tui/pane.py` migrates off the primary `on_event`/`on_state` slot too.

**Tech Stack:** Python 3.13, `httpx`, `markdown-it-py` (new), `pytest`, `pytest-asyncio`, `pytest-httpx`.

**Spec:** `docs/superpowers/specs/2026-05-26-aegis-telegram-renderer-correctness-design.md`

---

## File map

**Create:**
- `src/aegis/telegram/format_html.py` — markdown-it tokens → Telegram-HTML walker
- `tests/test_telegram_format_html.py` — HTML renderer unit tests
- `tests/test_telegram_send_document.py` — `sendDocument` bot test
- `tests/test_telegram_offset_persistence.py` — offset save/load tests
- `tests/test_telegram_frontend_e2e.py` — end-to-end MockBot test
- `tests/test_core_session_close.py` — `on_close` observer tests

**Replace:**
- `src/aegis/telegram/format.py` — chunker rewrite + `Spillover` type
- `tests/test_telegram_format.py` — drops `escape_md` + MarkdownV2 chunker tests; adds new chunker tests

**Modify:**
- `src/aegis/telegram/bot.py` — `parse_mode` param, `send_document`
- `src/aegis/telegram/frontend.py` — ticker, observers, offset, cleanup, overflow
- `src/aegis/core/session.py` — `on_close` + `add_close_observer`
- `src/aegis/tui/pane.py` — migrate primary slot to `add_*_observer`
- `src/aegis/cli.py` — thread `state_dir` into `TelegramFrontend`
- `tests/test_telegram_frontend.py` — drops `_refresh_loop` test; rewires ctor tests
- `pyproject.toml` — add `markdown-it-py`; bump version

---

## Task 1: Add markdown-it-py dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dep**

Edit `pyproject.toml` `[project].dependencies` list — add `"markdown-it-py>=3.0"` after `"httpx>=0.28"`:

```toml
dependencies = [
    "agent-client-protocol>=0.10",
    "croniter>=2.0",
    "fastmcp>=3.2.0",
    "httpx>=0.28",
    "markdown-it-py>=3.0",
    "ptyprocess>=0.7.0",
    ...
]
```

- [ ] **Step 2: Sync and verify import**

```bash
uv sync
uv run python -c "import markdown_it; print(markdown_it.__version__)"
```

Expected: prints a 3.x version, exit 0.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(telegram): add markdown-it-py for HTML render path"
```

---

## Task 2: format.py scaffold — Spillover type + module exports

**Files:**
- Replace: `src/aegis/telegram/format.py`
- Modify: `tests/test_telegram_format.py` — drop the MarkdownV2 tests

- [ ] **Step 1: Drop obsolete tests in `tests/test_telegram_format.py`**

Delete the following tests (they test `escape_md` and MarkdownV2 chunker behavior we're removing):
- `test_escape_md`
- `test_escape_md_escapes_backslash`
- `test_chunk_caps_and_labels`
- `test_chunk_single_no_label_noise`
- `test_chunk_multipart_is_valid_markdownv2`
- `test_chunk_truncation_suffix_is_valid_markdownv2`
- `test_chunk_does_not_split_escape_pair`

Keep `test_status_line_shape` — `status_line()` stays.

- [ ] **Step 2: Write a failing test for Spillover type**

Add to `tests/test_telegram_format.py`:

```python
from aegis.telegram.format import Spillover


def test_spillover_carries_raw_md_and_rendered_html():
    s = Spillover(raw_md="# title\n\nbody", rendered_html="<b>title</b>\n\nbody")
    assert s.raw_md == "# title\n\nbody"
    assert s.rendered_html == "<b>title</b>\n\nbody"
```

- [ ] **Step 3: Run — expect failure**

```bash
uv run pytest tests/test_telegram_format.py::test_spillover_carries_raw_md_and_rendered_html -v
```

Expected: ImportError on `Spillover`.

- [ ] **Step 4: Replace `src/aegis/telegram/format.py`**

```python
from __future__ import annotations

from typing import NamedTuple

from aegis.telegram.format_html import render as render_html


class Spillover(NamedTuple):
    raw_md: str
    rendered_html: str


def status_line(handle: str, state: str, model: str, metrics: str) -> str:
    icon = {"working": "⏳", "ready": "✅", "error": "⚠️"}.get(state, "•")
    return f"{icon} {handle} · {state} · {model} {metrics}"


def chunk(html: str, raw_md: str, *,
          max_parts: int = 3,
          limit: int = 4096) -> list[str] | Spillover:
    raise NotImplementedError  # Task 6


__all__ = ["Spillover", "status_line", "chunk", "render_html"]
```

- [ ] **Step 5: Stub `src/aegis/telegram/format_html.py`** to satisfy the import:

```python
from __future__ import annotations


def render(md: str) -> str:
    raise NotImplementedError  # Task 3–5
```

- [ ] **Step 6: Run — expect pass on the new test**

```bash
uv run pytest tests/test_telegram_format.py -v
```

Expected: `test_spillover_carries_raw_md_and_rendered_html` passes; `test_status_line_shape` still passes. Other deleted tests are gone.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/telegram/format.py src/aegis/telegram/format_html.py tests/test_telegram_format.py
git commit -m "refactor(telegram): scaffold format.py for HTML render + Spillover

Drops MarkdownV2 escape table and chunker; replaces with stub that
delegates render to format_html.render() (Task 3-5) and chunk()
(Task 6). Obsolete MarkdownV2 tests removed."
```

---

## Task 3: format_html — text escape + inline elements

**Files:**
- Modify: `src/aegis/telegram/format_html.py`
- Create: `tests/test_telegram_format_html.py`

Covers: plain text escaping, bold, italic, inline code, links, strikethrough, underline. Underline isn't standard CommonMark, but we treat `__text__` as bold (markdown-it default); `<u>` is reserved for explicit use only — skip for now.

- [ ] **Step 1: Write failing tests**

Create `tests/test_telegram_format_html.py`:

```python
from aegis.telegram.format_html import render


def test_plain_text_escapes_html_chars():
    assert render("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_bold():
    assert render("**bold**") == "<b>bold</b>"


def test_italic():
    assert render("*ital*") == "<i>ital</i>"


def test_inline_code_escapes_lt():
    assert render("hit `<script>` tag") == "hit <code>&lt;script&gt;</code> tag"


def test_link_escapes_href():
    out = render('see [docs](https://x.com/?a=1&b="2")')
    assert '<a href="https://x.com/?a=1&amp;b=&quot;2&quot;">docs</a>' in out


def test_strikethrough():
    assert render("~~gone~~") == "<s>gone</s>"


def test_nested_inline():
    assert render("**bold *and ital* end**") == "<b>bold <i>and ital</i> end</b>"
```

- [ ] **Step 2: Run — expect failure**

```bash
uv run pytest tests/test_telegram_format_html.py -v
```

Expected: NotImplementedError.

- [ ] **Step 3: Implement inline render**

Replace `src/aegis/telegram/format_html.py`:

```python
from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token

_MD = MarkdownIt("commonmark", {"breaks": False, "html": False}).enable("strikethrough")


def _esc_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    return _esc_text(s).replace('"', "&quot;")


# Inline token tag → Telegram-HTML tag pair (open, close).
_INLINE_TAGS: dict[str, tuple[str, str]] = {
    "strong": ("<b>", "</b>"),
    "em": ("<i>", "</i>"),
    "s": ("<s>", "</s>"),
}


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
                href = next((a[1] for a in (t.attrs.items() if hasattr(t.attrs, "items") else t.attrs) if a[0] == "href"), "")
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
    # For Task 3, only handle a single paragraph or inline-only input.
    out: list[str] = []
    for t in tokens:
        if t.type == "inline" and t.children:
            out.append(_render_inline(t.children))
        elif t.type == "paragraph_open":
            pass
        elif t.type == "paragraph_close":
            out.append("\n\n")
    return "".join(out).rstrip()
```

Note: `t.attrs` is sometimes a dict (newer markdown-it-py) and sometimes a list. The tuple-iter fallback handles both.

- [ ] **Step 4: Run — expect pass**

```bash
uv run pytest tests/test_telegram_format_html.py -v
```

Expected: all 7 pass.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/telegram/format_html.py tests/test_telegram_format_html.py
git commit -m "feat(telegram): format_html inline rendering (bold/italic/code/link/strike)"
```

---

## Task 4: format_html — block elements

**Files:**
- Modify: `src/aegis/telegram/format_html.py`
- Modify: `tests/test_telegram_format_html.py`

Covers: paragraphs, fenced code (with optional language tag), blockquote, headers (→ bold), unordered/ordered lists, horizontal rule.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_telegram_format_html.py`:

```python
def test_paragraph_separator():
    out = render("first\n\nsecond")
    assert out == "first\n\nsecond"


def test_fenced_code_with_language():
    md = "```python\nprint('hi')\n```"
    out = render(md)
    assert out == '<pre><code class="language-python">print(&#x27;hi&#x27;)\n</code></pre>'


def test_fenced_code_without_language():
    md = "```\nplain\n```"
    out = render(md)
    assert out == "<pre><code>plain\n</code></pre>"


def test_blockquote():
    assert render("> quoted line") == "<blockquote>quoted line</blockquote>"


def test_h1_flattens_to_bold():
    assert render("# Title") == "<b>Title</b>"


def test_h3_flattens_to_bold():
    assert render("### Subhead") == "<b>Subhead</b>"


def test_unordered_list():
    md = "- one\n- two"
    assert render(md) == "• one\n• two"


def test_ordered_list():
    md = "1. one\n2. two"
    assert render(md) == "1. one\n2. two"


def test_horizontal_rule():
    assert render("before\n\n---\n\nafter") == "before\n\n───────\n\nafter"


def test_mixed_block_and_inline():
    md = "Plain.\n\n## Heading\n\nBody with **bold**."
    assert render(md) == "Plain.\n\n<b>Heading</b>\n\nBody with <b>bold</b>."
```

- [ ] **Step 2: Run — expect failures**

```bash
uv run pytest tests/test_telegram_format_html.py -v
```

Expected: 7 fails; existing inline tests still pass. (Apostrophe gets HTML-entity escaped by markdown-it inside code blocks — that's `&#x27;`, which Telegram renders fine.)

- [ ] **Step 3: Extend render to handle block tokens**

Replace the body of `render()` in `format_html.py`:

```python
def render(md: str) -> str:
    tokens = _MD.parse(md)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "paragraph_open":
            inline = tokens[i + 1]
            out.append(_render_inline(inline.children or []))
            out.append("\n\n")
            i += 3  # paragraph_open, inline, paragraph_close
            continue
        if t.type == "heading_open":
            inline = tokens[i + 1]
            out.append("<b>")
            out.append(_render_inline(inline.children or []))
            out.append("</b>\n\n")
            i += 3
            continue
        if t.type == "fence":
            lang = (t.info or "").strip().split(maxsplit=1)[0]
            body = _esc_text(t.content).rstrip("\n") + "\n"
            if lang:
                out.append(f'<pre><code class="language-{_esc_attr(lang)}">{body}</code></pre>')
            else:
                out.append(f"<pre><code>{body}</code></pre>")
            out.append("\n\n")
            i += 1
            continue
        if t.type == "blockquote_open":
            # find the matching close, render the inside, wrap it
            depth = 1
            j = i + 1
            inner: list[Token] = []
            while j < len(tokens) and depth > 0:
                if tokens[j].type == "blockquote_open":
                    depth += 1
                elif tokens[j].type == "blockquote_close":
                    depth -= 1
                    if depth == 0:
                        break
                inner.append(tokens[j])
                j += 1
            inside = render_tokens(inner).rstrip("\n")
            out.append(f"<blockquote>{inside}</blockquote>\n\n")
            i = j + 1
            continue
        if t.type == "bullet_list_open" or t.type == "ordered_list_open":
            ordered = t.type == "ordered_list_open"
            j = i + 1
            n = 1
            while j < len(tokens) and tokens[j].type != ("ordered_list_close" if ordered else "bullet_list_close"):
                if tokens[j].type == "list_item_open":
                    # find the matching close, render inline content inside
                    k = j + 1
                    depth = 1
                    item_inline = ""
                    while k < len(tokens) and depth > 0:
                        if tokens[k].type == "list_item_open":
                            depth += 1
                        elif tokens[k].type == "list_item_close":
                            depth -= 1
                            if depth == 0:
                                break
                        elif tokens[k].type == "inline":
                            item_inline += _render_inline(tokens[k].children or [])
                        k += 1
                    prefix = f"{n}. " if ordered else "• "
                    out.append(f"{prefix}{item_inline}\n")
                    n += 1
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
        i += 1
    return "".join(out).rstrip()


def render_tokens(tokens: list[Token]) -> str:
    """Render a slice of tokens (used recursively by blockquote handling)."""
    # Reuse the top-level walker with a stub markdown that hands us back the slice.
    # Implementation note: extract the walker body into _walk_tokens for reuse.
    return _walk_tokens(tokens)


def _walk_tokens(tokens: list[Token]) -> str:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            if inline and inline.type == "inline":
                out.append(_render_inline(inline.children or []))
                out.append("\n\n")
                i += 3
                continue
        if t.type == "inline":
            out.append(_render_inline(t.children or []))
            i += 1
            continue
        i += 1
    return "".join(out)
```

Refactor `render()` to delegate to `_walk_tokens()` so both call sites share logic — replace the manual paragraph loop with a single call to `_walk_tokens()` covering all block types. (The version above shows both for clarity; the actual final file should have one walker that handles all blocks, called from `render()` and `render_tokens()`.)

- [ ] **Step 4: Run — expect pass**

```bash
uv run pytest tests/test_telegram_format_html.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/telegram/format_html.py tests/test_telegram_format_html.py
git commit -m "feat(telegram): format_html block elements (paragraphs/headers/fence/lists/blockquote)"
```

---

## Task 5: format_html — tables and images

**Files:**
- Modify: `src/aegis/telegram/format_html.py`
- Modify: `tests/test_telegram_format_html.py`

- [ ] **Step 1: Add failing tests**

```python
def test_table_renders_as_pre():
    md = "| h1 | h2 |\n|----|----|\n| a  | b  |"
    out = render(md)
    # Tables flatten to a <pre> block carrying the source markdown.
    assert out.startswith("<pre>")
    assert "h1" in out and "h2" in out and "a" in out and "b" in out
    assert out.endswith("</pre>")


def test_image_renders_as_placeholder():
    md = "![alt text](https://example.com/img.png)"
    out = render(md)
    assert "[image: alt text]" in out
```

- [ ] **Step 2: Run — expect failures.**

- [ ] **Step 3: Enable the table rule in markdown-it and handle the table tokens**

```python
_MD = (MarkdownIt("commonmark", {"breaks": False, "html": False})
       .enable("strikethrough")
       .enable("table"))
```

Add table/image handling in `_walk_tokens`:

```python
if t.type == "table_open":
    # Walk to table_close, emit raw source as a <pre> block.
    j = i
    src_start = t.map[0] if t.map else None
    src_end = t.map[1] if t.map else None
    while j < len(tokens) and tokens[j].type != "table_close":
        j += 1
    # Use the original source slice for the table — easier than reconstructing.
    if src_start is not None and src_end is not None:
        src = "\n".join(md_source.splitlines()[src_start:src_end])
        out.append(f"<pre>{_esc_text(src)}</pre>\n\n")
    i = j + 1
    continue
```

This requires `_walk_tokens` to know the source. Refactor: pass `md_source` to `_walk_tokens`, store it once at the top of `render()`. Update recursive call in blockquote handling to pass it through.

For inline images: in `_render_inline`, handle `image` tokens:

```python
elif t.type == "image":
    alt = t.content or ""
    parts.append(f"[image: {_esc_text(alt)}]")
```

- [ ] **Step 4: Run — expect pass.**

- [ ] **Step 5: Commit**

```bash
git add src/aegis/telegram/format_html.py tests/test_telegram_format_html.py
git commit -m "feat(telegram): format_html tables→pre, images→placeholder"
```

---

## Task 6: format.chunk() — greedy pack with Spillover

**Files:**
- Modify: `src/aegis/telegram/format.py`
- Modify: `tests/test_telegram_format.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_telegram_format.py`:

```python
from aegis.telegram.format import chunk, Spillover


def test_chunk_single_paragraph_fits():
    html = "hello world"
    out = chunk(html, raw_md="hello world")
    assert out == ["hello world"]


def test_chunk_packs_two_paragraphs_into_one_part_if_fits():
    html = "para one\n\npara two"
    out = chunk(html, raw_md="para one\n\npara two")
    assert out == ["para one\n\npara two"]


def test_chunk_splits_into_two_parts_when_exceeds_limit():
    a = "x" * 3000
    b = "y" * 3000
    html = f"{a}\n\n{b}"
    out = chunk(html, raw_md=html, limit=4096)
    assert isinstance(out, list)
    assert len(out) == 2
    assert out[0] == a
    assert out[1] == b


def test_chunk_returns_spillover_when_more_than_3_parts():
    parts = [f"p{i}-" + "x" * 3500 for i in range(5)]
    html = "\n\n".join(parts)
    out = chunk(html, raw_md=html, limit=4096)
    assert isinstance(out, Spillover)
    assert out.raw_md == html
    assert out.rendered_html == html


def test_chunk_returns_spillover_when_single_pre_exceeds_limit():
    big_code = "x" * 5000
    html = f"<pre><code>{big_code}</code></pre>"
    out = chunk(html, raw_md="```\n" + big_code + "\n```", limit=4096)
    assert isinstance(out, Spillover)


def test_chunk_never_splits_inside_pre():
    pre = "<pre><code>" + "x" * 1000 + "</code></pre>"
    other = "y" * 2000
    html = f"{pre}\n\n{other}"
    out = chunk(html, raw_md=html, limit=4096)
    assert isinstance(out, list)
    assert len(out) == 1  # both fit in one part
    assert pre in out[0]
```

- [ ] **Step 2: Run — expect failure (NotImplementedError).**

- [ ] **Step 3: Implement `chunk()` in `format.py`**

```python
def chunk(html: str, raw_md: str, *,
          max_parts: int = 3,
          limit: int = 4096) -> list[str] | Spillover:
    blocks = _split_blocks(html)
    # Guard: any single <pre> block exceeding limit forces spillover.
    for b in blocks:
        if len(b) > limit and b.startswith("<pre>"):
            return Spillover(raw_md=raw_md, rendered_html=html)
    parts = _greedy_pack(blocks, limit=limit)
    if len(parts) > max_parts:
        return Spillover(raw_md=raw_md, rendered_html=html)
    return parts


def _split_blocks(html: str) -> list[str]:
    """Split into atomic blocks (paragraphs / pre / blockquote) on \\n\\n
    boundaries, but never inside a <pre>...</pre>."""
    blocks: list[str] = []
    i = 0
    n = len(html)
    while i < n:
        if html.startswith("<pre>", i):
            end = html.find("</pre>", i)
            if end == -1:
                blocks.append(html[i:])
                break
            end += len("</pre>")
            blocks.append(html[i:end])
            i = end
            # skip following \n\n
            while i < n and html[i] in "\n":
                i += 1
        else:
            # find next \n\n that's not inside a <pre>
            j = i
            while j < n:
                if html.startswith("<pre>", j):
                    break
                if html.startswith("\n\n", j):
                    break
                j += 1
            blocks.append(html[i:j])
            i = j
            while i < n and html[i] in "\n":
                i += 1
    return [b for b in blocks if b]


def _greedy_pack(blocks: list[str], *, limit: int) -> list[str]:
    parts: list[str] = []
    cur = ""
    for b in blocks:
        candidate = b if not cur else cur + "\n\n" + b
        if len(candidate) <= limit:
            cur = candidate
        else:
            if cur:
                parts.append(cur)
            cur = b
    if cur:
        parts.append(cur)
    return parts
```

- [ ] **Step 4: Run — expect pass**

```bash
uv run pytest tests/test_telegram_format.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/telegram/format.py tests/test_telegram_format.py
git commit -m "feat(telegram): greedy chunker with Spillover for >3-part replies"
```

---

## Task 7: bot.py — parse_mode parameter

**Files:**
- Modify: `src/aegis/telegram/bot.py`
- Modify: `tests/test_telegram_bot.py`
- Modify: `src/aegis/telegram/frontend.py:95` — replace `markdown=True` callsite (transitional)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_telegram_bot.py`:

```python
async def test_send_message_html_parse_mode(monkeypatch):
    seen = {}
    async def fake_call(self, method, **params):
        seen["method"] = method
        seen["params"] = params
        return {"message_id": 99}
    monkeypatch.setattr("aegis.telegram.bot.BotClient._call", fake_call)
    bot = BotClient(token="t")
    mid = await bot.send_message(chat_id=1, text="<b>x</b>", parse_mode="HTML")
    assert mid == 99
    assert seen["params"]["parse_mode"] == "HTML"


async def test_send_message_no_parse_mode_when_none(monkeypatch):
    seen = {}
    async def fake_call(self, method, **params):
        seen["params"] = params
        return {"message_id": 1}
    monkeypatch.setattr("aegis.telegram.bot.BotClient._call", fake_call)
    bot = BotClient(token="t")
    await bot.send_message(chat_id=1, text="plain")
    assert "parse_mode" not in seen["params"]


async def test_edit_message_html_parse_mode(monkeypatch):
    seen = {}
    async def fake_call(self, method, **params):
        seen["params"] = params
        return {}
    monkeypatch.setattr("aegis.telegram.bot.BotClient._call", fake_call)
    bot = BotClient(token="t")
    await bot.edit_message(chat_id=1, message_id=2, text="<i>x</i>", parse_mode="HTML")
    assert seen["params"]["parse_mode"] == "HTML"
```

- [ ] **Step 2: Run — expect failures (TypeError / missing param).**

- [ ] **Step 3: Update `bot.py`**

```python
async def send_message(self, chat_id: int, text: str,
                       *, parse_mode: str | None = None) -> int | None:
    params: dict = {"chat_id": chat_id, "text": text}
    if parse_mode is not None:
        params["parse_mode"] = parse_mode
    res = await self._call("sendMessage", **params)
    return res["message_id"] if res else None


async def edit_message(self, chat_id: int, message_id: int, text: str,
                       *, parse_mode: str | None = None) -> None:
    params: dict = {"chat_id": chat_id,
                    "message_id": message_id,
                    "text": text}
    if parse_mode is not None:
        params["parse_mode"] = parse_mode
    await self._call("editMessageText", **params)
```

Remove the `markdown: bool = False` parameter entirely. No backward-compat shim.

- [ ] **Step 4: Update the one current `markdown=True` caller**

In `src/aegis/telegram/frontend.py`, find `await self._bot.send_message(self._chat, part, markdown=True)` and change to `await self._bot.send_message(self._chat, part, parse_mode="HTML")`. (Transitional — the real render path overhaul lands in Tasks 11–14.)

- [ ] **Step 5: Run — expect pass on bot tests; existing frontend tests may break**

```bash
uv run pytest tests/test_telegram_bot.py -v
```

Expected: new bot tests pass.

```bash
uv run pytest tests/test_telegram_frontend.py -v
```

Frontend tests that check for `markdown=True` will fail; their cleanup is part of Task 11. Acceptable interim state.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/telegram/bot.py src/aegis/telegram/frontend.py tests/test_telegram_bot.py
git commit -m "feat(telegram): bot parse_mode param; remove markdown bool"
```

---

## Task 8: bot.send_document — multipart POST

**Files:**
- Modify: `src/aegis/telegram/bot.py`
- Create: `tests/test_telegram_send_document.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_telegram_send_document.py
import pytest
import httpx
from aegis.telegram.bot import BotClient


async def test_send_document_multipart(tmp_path):
    f = tmp_path / "reply.md"
    f.write_text("# hello\n\nbody")
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["url"] = str(req.url)
        seen["content_type"] = req.headers.get("content-type", "")
        seen["body"] = req.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.telegram.org")
    bot = BotClient(token="t", http=http)
    mid = await bot.send_document(chat_id=1, path=f, caption="see attached", parse_mode="HTML")
    assert mid == 77
    assert seen["method"] == "POST"
    assert "sendDocument" in seen["url"]
    assert "multipart/form-data" in seen["content_type"]
    assert b'name="chat_id"' in seen["body"]
    assert b'name="caption"' in seen["body"]
    assert b'name="parse_mode"' in seen["body"]
    assert b'name="document"' in seen["body"]
    assert b"# hello" in seen["body"]
```

- [ ] **Step 2: Run — expect failure (AttributeError on send_document).**

- [ ] **Step 3: Implement send_document**

In `bot.py`, add:

```python
from pathlib import Path

async def send_document(self, chat_id: int, path: Path, *,
                        caption: str | None = None,
                        parse_mode: str | None = None) -> int | None:
    url = self._url("sendDocument")
    data: dict[str, str] = {"chat_id": str(chat_id)}
    if caption is not None:
        data["caption"] = caption
    if parse_mode is not None:
        data["parse_mode"] = parse_mode
    for attempt in range(5):
        try:
            with path.open("rb") as fp:
                files = {"document": (path.name, fp, "text/markdown")}
                r = await self._http.post(url, data=data, files=files)
        except httpx.HTTPError as e:
            wait = min(2 ** attempt, 30)
            log.warning("telegram sendDocument network error: %s (retry %ss)", e, wait)
            await asyncio.sleep(wait)
            continue
        if r.status_code == 429:
            ra = r.json().get("parameters", {}).get("retry_after", 1)
            await asyncio.sleep(ra)
            continue
        body = r.json()
        if not body.get("ok"):
            log.warning("telegram sendDocument !ok: %s", body)
            return None
        return body["result"]["message_id"]
    log.error("telegram sendDocument gave up after retries")
    return None
```

- [ ] **Step 4: Run — expect pass**

```bash
uv run pytest tests/test_telegram_send_document.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/telegram/bot.py tests/test_telegram_send_document.py
git commit -m "feat(telegram): bot.send_document multipart upload"
```

---

## Task 9: session.on_close + add_close_observer + add_inbox_observer

**Files:**
- Modify: `src/aegis/core/session.py`
- Create: `tests/test_core_session_close.py`

Adds two new observer patterns matching the existing two:
- `add_close_observer` — new event class (fires from teardown paths).
- `add_inbox_observer` — adds the extras-list pattern for `on_inbox`,
  which `tui/pane.py:266` currently claims via the primary slot. The
  Telegram envelope listener (Task 13) needs to coexist with TUI's
  existing inbox observer.

- [ ] **Step 1: Write failing test**

```python
# tests/test_core_session_close.py
from aegis.core.session import AgentSession  # adjust import to actual class name


def test_on_close_fires_on_primary_and_extras():
    # Use whatever existing fixture pattern test_core_session.py uses.
    # Simplified for illustration:
    session = make_session()
    calls = []
    session.on_close = lambda s, reason: calls.append(("primary", reason))
    session.add_close_observer(lambda s, reason: calls.append(("extra1", reason)))
    session.add_close_observer(lambda s, reason: calls.append(("extra2", reason)))
    session._emit_close("explicit")
    assert calls == [("primary", "explicit"), ("extra1", "explicit"), ("extra2", "explicit")]


def test_one_extra_raising_does_not_break_others():
    session = make_session()
    calls = []
    session.add_close_observer(lambda s, reason: (_ for _ in ()).throw(RuntimeError("boom")))
    session.add_close_observer(lambda s, reason: calls.append("survived"))
    session._emit_close("crash")
    assert calls == ["survived"]
```

Look at `tests/test_core_session.py` for the actual session-construction fixture and follow that pattern.

- [ ] **Step 2: Run — expect failure (AttributeError on on_close).**

- [ ] **Step 3: Add to `session.py`**

In the `__init__`:

```python
self.on_close: CloseCb | None = None
self._extra_close_observers: list[CloseCb] = []
self._extra_inbox_observers: list[InboxCb] = []  # on_inbox primary slot already exists
```

Add `CloseCb` to the type aliases at the top of the file: `CloseCb = Callable[["AgentSession", str], None]`.

Add the methods:

```python
def add_close_observer(self, cb: CloseCb) -> None:
    """Subscribe an additional close callback. Fires after on_close."""
    self._extra_close_observers.append(cb)

def add_inbox_observer(self, cb: InboxCb) -> None:
    """Subscribe an additional inbox callback. Fires after on_inbox."""
    self._extra_inbox_observers.append(cb)

def _emit_close(self, reason: str) -> None:
    if self.on_close is not None:
        try:
            self.on_close(self, reason)
        except Exception:
            log.exception("on_close raised; continuing")
    for cb in self._extra_close_observers:
        try:
            cb(self, reason)
        except Exception:
            log.exception("close observer raised; continuing")
```

Update the existing `deliver()` method at session.py line 90-91 to fire
extras after the primary:

```python
if self.on_inbox is not None:
    try: self.on_inbox(self, msg)
    except Exception: log.exception("on_inbox raised; continuing")
for cb in self._extra_inbox_observers:
    try: cb(self, msg)
    except Exception: log.exception("inbox observer raised; continuing")
```

- [ ] **Step 4: Wire `_emit_close` into existing session-teardown paths**

Search for the existing close/teardown code:

```bash
grep -n "self._started\s*=\s*False\|_session.close\|self._task.*cancel" src/aegis/core/session.py
```

At each genuine teardown site, call `self._emit_close(reason)` with the right reason string (`"explicit"` for user-triggered `/close`, `"crash"` for the exception-recovery path, `"teardown"` for substrate shutdown). If there's only one close path today, only that site gets the call; the others (`handoff`) can be added when those flows land.

- [ ] **Step 5: Run — expect pass**

```bash
uv run pytest tests/test_core_session_close.py tests/test_core_session.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/aegis/core/session.py tests/test_core_session_close.py
git commit -m "feat(core/session): add on_close + add_close_observer"
```

---

## Task 10: tui/pane.py — migrate to add_*_observer

**Files:**
- Modify: `src/aegis/tui/pane.py`

- [ ] **Step 1: Read current state**

```bash
sed -n '260,270p' src/aegis/tui/pane.py
```

Confirm three lines: `self._core.on_event = self._on_core_event`, `self._core.on_state = self._on_core_state`, and `self._core.on_inbox = self._on_core_inbox`.

- [ ] **Step 2: Replace with multi-observer registration**

```python
# Before:
self._core.on_event = self._on_core_event
self._core.on_state = self._on_core_state
self._core.on_inbox = self._on_core_inbox

# After:
self._core.add_event_observer(self._on_core_event)
self._core.add_state_observer(self._on_core_state)
self._core.add_inbox_observer(self._on_core_inbox)
```

- [ ] **Step 3: Run TUI test suite**

```bash
uv run pytest tests/test_tui.py tests/test_pane_replay.py -v
```

Expected: all pass. (Existing tests don't depend on which observer slot is used.)

- [ ] **Step 4: Commit**

```bash
git add src/aegis/tui/pane.py
git commit -m "refactor(tui/pane): migrate observers off primary slot

Frees the primary on_event/on_state slot so multiple frontends
(TUI + Telegram) can observe the same session without clobbering."
```

---

## Task 11: telegram/frontend — per-handle state + observer migration

**Files:**
- Modify: `src/aegis/telegram/frontend.py`
- Modify: `tests/test_telegram_frontend.py`

This is the biggest structural change. Replaces the closure-based per-core `state = {...}` with a frontend-owned dict keyed by handle.

- [ ] **Step 1: Delete obsolete tests**

In `tests/test_telegram_frontend.py`, delete:
- `test_mid_turn_refresher_edits_status_repeatedly` — refresher goes away in Task 12

Keep all others.

- [ ] **Step 2: Add new test for per-handle state**

```python
async def test_two_sessions_have_independent_state(frontend, manager):
    # Spawn two sessions, both reach AgentState.working — frontend should
    # track each independently and not collide on `mid`.
    a = await manager.spawn("a", ...)
    b = await manager.spawn("b", ...)
    # Drive both to working state via the frontend's state observer.
    # Both `_states["a"]["mid"]` and `_states["b"]["mid"]` should be set
    # to distinct message ids.
    ...
```

Pattern is sketched — implement based on the existing fixture in the file.

- [ ] **Step 3: Refactor `frontend.py`**

```python
class TelegramFrontend:
    def __init__(self, bot, manager, bridge, cfg, *, chat_id,
                 auto_prompt, state_dir: Path):
        self._bot = bot
        self._m = manager
        self._bridge = bridge
        self._cfg = cfg
        self._chat = chat_id
        self._auto = auto_prompt
        self._state_dir = state_dir
        self._active: str | None = None
        # Per-handle turn state: {"mid": int|None, "envelope": str|None,
        #                        "tool_counts": dict[str, int], "buf": list[str]}
        self._states: dict[str, dict] = {}

    def _state_for(self, handle: str) -> dict:
        return self._states.setdefault(handle, {
            "mid": None, "envelope": None, "tool_counts": {}, "buf": [],
        })

    def _attach_observers(self, core) -> None:
        if getattr(core, "_tg_wired", False):
            return
        core._tg_wired = True

        def on_event(c, ev):
            asyncio.create_task(self._on_event(c, ev))
        def on_state(c, st, finished):
            asyncio.create_task(self._on_state(c, st, finished))

        core.add_event_observer(on_event)
        core.add_state_observer(on_state)
```

Drop `refresh_interval` from `__init__`. Drop `_refresh_loop` (Task 12 replaces it). Move `state` from closure to `self._state_for(core.handle)`.

- [ ] **Step 4: Update `cli.py` to pass `state_dir`**

```bash
grep -n "TelegramFrontend(" src/aegis/cli.py
```

Add the `state_dir=<existing state dir path>` kwarg to the construction site. Use the same path the rest of aegis uses (search for `state_dir` or `.aegis/state` in `cli.py`).

- [ ] **Step 5: Update remaining frontend tests**

For each existing test in `tests/test_telegram_frontend.py` that constructs `TelegramFrontend(...)`, pass `state_dir=tmp_path` (use the pytest `tmp_path` fixture). Drop any `refresh_interval=` kwarg.

- [ ] **Step 6: Run frontend tests**

```bash
uv run pytest tests/test_telegram_frontend.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/telegram/frontend.py src/aegis/cli.py tests/test_telegram_frontend.py
git commit -m "refactor(telegram): per-handle state + add_*_observer migration

State moves from closure-per-core to frontend-owned dict keyed by
handle. Observer registration uses add_event_observer /
add_state_observer instead of primary slot, freeing it for the TUI."
```

---

## Task 12: telegram/frontend — event-driven ticker

**Files:**
- Modify: `src/aegis/telegram/frontend.py`
- Modify: `tests/test_telegram_frontend.py`

Replace the `_refresh_loop` + 2s edits with edits triggered on `ToolUse` / `ToolResult` / state transitions.

- [ ] **Step 1: Write failing test**

```python
async def test_ticker_edits_on_tool_use_not_on_timer(frontend, manager, bot):
    # Spawn agent, drive into working state, emit two ToolUse events,
    # finish turn. Assert exact bot calls:
    #   send_message(<thinking…>)
    #   edit_message(<🔧 ToolA x1>)
    #   edit_message(<🔧 ToolA x1, ToolB x1>)
    #   edit_message(<✅ ToolA x1, ToolB x1>)
    #   send_message(<reply>)
    ...
```

Pattern follows existing frontend tests.

- [ ] **Step 2: Implement event-driven ticker**

In `frontend.py`:

```python
async def _on_event(self, core, ev) -> None:
    from aegis.events import AssistantText, ToolUse
    state = self._state_for(core.handle)
    if isinstance(ev, AssistantText):
        state["buf"].append(ev.text)
    elif isinstance(ev, ToolUse):
        counts = state["tool_counts"]
        counts[ev.name] = counts.get(ev.name, 0) + 1
        await self._edit_ticker(core)

async def _edit_ticker(self, core) -> None:
    state = self._state_for(core.handle)
    mid = state.get("mid")
    if mid is None:
        return
    text = self._render_ticker(core, state)
    try:
        await self._bot.edit_message(self._chat, mid, text, parse_mode="HTML")
    except Exception:
        log.exception("ticker edit failed; turn proceeds without further updates")

def _render_ticker(self, core, state) -> str:
    icon = {"working": "🔧", "ready": "✅", "error": "⚠️"}.get(core.state.value, "⏳")
    if state["tool_counts"]:
        counts_str = ", ".join(f"{n} x{c}" for n, c in state["tool_counts"].items())
    else:
        counts_str = "thinking…"
    envelope = state.get("envelope")
    prefix = f"✉️ {envelope} · " if envelope else ""
    return f"{prefix}{icon} {counts_str}"

async def _on_state(self, core, st, finished) -> None:
    state = self._state_for(core.handle)
    from aegis.tui.state import AgentState
    if st is AgentState.working and state["mid"] is None:
        text = self._render_ticker(core, state)
        try:
            mid = await self._bot.send_message(self._chat, text, parse_mode="HTML")
        except Exception:
            log.exception("status send failed; turn proceeds without ticker")
            mid = None
        if mid is None:
            log.warning("send_message returned None; no ticker for this turn")
        else:
            state["mid"] = mid
    elif state["mid"] is not None:
        await self._edit_ticker(core)
    if finished:
        reply_md = "".join(state["buf"]).strip() or "(no output)"
        await self._send_reply(core, reply_md, state)
        # reset per-turn fields, keep mid for the next turn? No — turn ends,
        # mid becomes a stale anchor. Clear.
        state["mid"] = None
        state["envelope"] = None
        state["tool_counts"] = {}
        state["buf"] = []
```

`_send_reply` is filled in by Task 14 (chunk + spillover). Stub for now:

```python
async def _send_reply(self, core, reply_md, state) -> None:
    from aegis.telegram.format import render_html
    html = render_html(reply_md)
    await self._bot.send_message(self._chat, html, parse_mode="HTML")
```

- [ ] **Step 3: Delete `_refresh_loop` and its task plumbing.**

- [ ] **Step 4: Run frontend tests**

```bash
uv run pytest tests/test_telegram_frontend.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/telegram/frontend.py tests/test_telegram_frontend.py
git commit -m "feat(telegram): event-driven ticker replaces 2s refresh loop

Status message edits on ToolUse / state transitions instead of every
2 seconds. Eliminates the rate-limit silent freeze on long turns
(finding D-#4) and surfaces tool-call activity live on the chip
(finding #11)."
```

---

## Task 13: telegram/frontend — envelope detection via on_inbox

**Files:**
- Modify: `src/aegis/telegram/frontend.py`
- Modify: `tests/test_telegram_frontend.py`

- [ ] **Step 1: Write failing test**

```python
async def test_envelope_shows_on_ticker(frontend, manager, bot):
    # Drive an InboxMessage at a session via core.deliver(msg).
    # Assert the next send_message status text contains "✉️ from".
    ...
```

- [ ] **Step 2: Register an inbox observer in `_attach_observers`**

```python
def _on_inbox(c, msg):
    s = self._state_for(c.handle)
    sender = msg.sender if hasattr(msg, "sender") else None
    if sender:
        s["envelope"] = f"from {sender.kind}:{sender.handle}:{sender.queue}"

core.add_inbox_observer(_on_inbox)
```

Uses `add_inbox_observer` from Task 9 so TUI (which already claims the primary `on_inbox` slot at `tui/pane.py:266`) keeps working. Task 10 migrates TUI to `add_inbox_observer` too.

- [ ] **Step 3: Run frontend tests**

```bash
uv run pytest tests/test_telegram_frontend.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/aegis/telegram/frontend.py tests/test_telegram_frontend.py
git commit -m "feat(telegram): surface inbox envelope on ticker via on_inbox"
```

---

## Task 14: telegram/frontend — overflow send path

**Files:**
- Modify: `src/aegis/telegram/frontend.py`
- Modify: `tests/test_telegram_frontend.py`

- [ ] **Step 1: Write failing test**

```python
async def test_overflow_replies_as_send_document(frontend, manager, bot, tmp_path):
    # Force a reply that needs >3 parts.
    # Assert bot.send_document was called with a .md path matching
    # state_dir/overflow/aegis-reply-*.md, caption starts with first
    # 500 chars of the reply, parse_mode="HTML".
    ...
```

- [ ] **Step 2: Implement `_send_reply` overflow branch**

```python
import datetime as _dt

async def _send_reply(self, core, reply_md, state) -> None:
    from aegis.telegram.format import chunk, render_html, Spillover

    html = render_html(reply_md)
    parts_or_spill = chunk(html, raw_md=reply_md)
    if isinstance(parts_or_spill, Spillover):
        path = self._write_overflow(core.handle, reply_md)
        peek_md = reply_md[:500]
        peek_html = render_html(peek_md)
        caption = (f"<i>{core.handle}</i>\n\n{peek_html}\n\n…\n\n"
                   f"📎 Full response ({len(reply_md)} chars) attached.")
        await self._bot.send_document(self._chat, path, caption=caption,
                                       parse_mode="HTML")
        return
    parts = parts_or_spill
    for i, part in enumerate(parts):
        if len(parts) > 1:
            label = f"<i>{core.handle} ({i+1}/{len(parts)})</i>\n"
            text = label + part
        else:
            text = part
        await self._bot.send_message(self._chat, text, parse_mode="HTML")

def _write_overflow(self, handle: str, raw_md: str) -> Path:
    folder = self._state_dir / "overflow"
    folder.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    path = folder / f"aegis-reply-{ts}-{handle}.md"
    path.write_text(raw_md)
    return path
```

Note Telegram's caption cap is 1024 chars — a 500-char peek rendered to HTML can blow past that if the markdown is dense with formatting. Add a safety truncation:

```python
if len(caption) > 1024:
    caption = caption[:1000] + "…\n\n📎 attached."
```

- [ ] **Step 3: Run frontend tests**

```bash
uv run pytest tests/test_telegram_frontend.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/aegis/telegram/frontend.py tests/test_telegram_frontend.py
git commit -m "feat(telegram): spillover overflow as .md attachment with peek caption"
```

---

## Task 15: telegram/frontend — offset persistence

**Files:**
- Modify: `src/aegis/telegram/frontend.py`
- Create: `tests/test_telegram_offset_persistence.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_telegram_offset_persistence.py
from pathlib import Path
from aegis.telegram.frontend import TelegramFrontend


def make_frontend(tmp_path: Path) -> TelegramFrontend:
    # minimal ctor — pass None / stubs for non-offset deps
    return TelegramFrontend(bot=None, manager=None, bridge=None, cfg=None,
                            chat_id=1, auto_prompt="", state_dir=tmp_path)


def test_load_offset_missing_returns_zero(tmp_path):
    f = make_frontend(tmp_path)
    assert f._load_offset() == 0


def test_save_then_load(tmp_path):
    f = make_frontend(tmp_path)
    f._save_offset(42)
    assert f._load_offset() == 42


def test_load_corrupt_returns_zero(tmp_path, caplog):
    (tmp_path / "telegram.offset").write_text("not-a-number")
    f = make_frontend(tmp_path)
    assert f._load_offset() == 0
    assert "corrupt" in caplog.text.lower()


def test_save_atomic(tmp_path):
    f = make_frontend(tmp_path)
    f._save_offset(7)
    assert not list(tmp_path.glob("*.tmp"))
    assert (tmp_path / "telegram.offset").read_text().strip() == "7"
```

- [ ] **Step 2: Implement in `frontend.py`**

```python
def _offset_path(self) -> Path:
    return self._state_dir / "telegram.offset"

def _load_offset(self) -> int:
    try:
        return int(self._offset_path().read_text().strip())
    except FileNotFoundError:
        return 0
    except ValueError:
        log.warning("telegram.offset corrupt; starting at 0")
        return 0

def _save_offset(self, offset: int) -> None:
    p = self._offset_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(f"{offset}\n")
    tmp.replace(p)

async def run(self, bot) -> None:
    offset = self._load_offset()
    while True:
        for up in await bot.get_updates(offset):
            offset = up["update_id"] + 1
            self._save_offset(offset)
            try:
                await self.handle_update(up)
            except Exception:
                log.exception("update handling failed")
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/test_telegram_offset_persistence.py tests/test_telegram_frontend.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/aegis/telegram/frontend.py tests/test_telegram_offset_persistence.py
git commit -m "feat(telegram): persist update offset to state_dir/telegram.offset

Survives restart without replaying the last 24h of Telegram updates
(finding D-#2)."
```

---

## Task 16: telegram/frontend — close observer + _active cleanup

**Files:**
- Modify: `src/aegis/telegram/frontend.py`
- Modify: `tests/test_telegram_frontend.py`

- [ ] **Step 1: Write failing test**

```python
async def test_active_clears_on_session_close(frontend, manager):
    core = await manager.spawn("a", ...)
    frontend._active = "a"
    # Simulate substrate-driven close — call _emit_close on the session.
    core._emit_close("teardown")
    assert frontend._active is None
```

- [ ] **Step 2: Register a close observer in `_attach_observers`**

```python
def _on_close(c, reason):
    self._states.pop(c.handle, None)
    if self._active == c.handle:
        self._active = None

core.add_close_observer(_on_close)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/test_telegram_frontend.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/aegis/telegram/frontend.py tests/test_telegram_frontend.py
git commit -m "feat(telegram): clear _active on session close (D-#3)

Listens via add_close_observer; clears _active + per-handle state when
the named session tears down by any path (explicit, crash, handoff,
teardown)."
```

---

## Task 17: End-to-end MockBot integration

**Files:**
- Create: `tests/test_telegram_frontend_e2e.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/test_telegram_frontend_e2e.py
import pytest
from pathlib import Path

from aegis.telegram.frontend import TelegramFrontend
# imports for session manager, events, bridge stubs — match patterns from
# tests/test_telegram_frontend.py and tests/conftest.py


class MockBot:
    def __init__(self):
        self.calls: list[tuple] = []
        self._next_mid = 100

    async def send_message(self, chat_id, text, *, parse_mode=None):
        self.calls.append(("send_message", chat_id, text, parse_mode))
        self._next_mid += 1
        return self._next_mid

    async def edit_message(self, chat_id, message_id, text, *, parse_mode=None):
        self.calls.append(("edit_message", chat_id, message_id, text, parse_mode))

    async def send_document(self, chat_id, path, *, caption=None, parse_mode=None):
        self.calls.append(("send_document", chat_id, path, caption, parse_mode))
        self._next_mid += 1
        return self._next_mid

    async def get_updates(self, offset, timeout=50):
        return []


async def test_e2e_simple_reply(tmp_path):
    # Spin up frontend + session manager, drive a single turn that emits
    # two ToolUse events and one AssistantText. Assert exact MockBot.calls
    # sequence per spec:
    #   1. send_message(thinking…)
    #   2. edit_message(🔧 ToolA x1)
    #   3. edit_message(🔧 ToolA x1, ToolB x1)
    #   4. edit_message(✅ ToolA x1, ToolB x1)
    #   5. send_message(<rendered reply HTML>)
    ...


async def test_e2e_overflow_reply(tmp_path):
    # Same setup but the AssistantText is >3 parts worth of content.
    # Assert calls end with a send_document instead of multi-part send_message.
    ...


async def test_e2e_two_frontends_share_session(tmp_path):
    # Register both a TUI-style observer (a list) and the TelegramFrontend
    # against the same core. Emit events. Assert both see every event.
    ...
```

The actual integration is involved — follow the patterns in `tests/test_telegram_frontend.py` and `tests/conftest.py` to build the session-manager + core fixtures. Use a fake driver / event emitter.

- [ ] **Step 2: Run — expect pass**

```bash
uv run pytest tests/test_telegram_frontend_e2e.py -v
```

- [ ] **Step 3: Run the full telegram test suite to catch regressions**

```bash
uv run pytest tests/test_telegram_*.py tests/test_core_session.py tests/test_core_session_close.py -v
```

All must pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_telegram_frontend_e2e.py
git commit -m "test(telegram): end-to-end integration via MockBot

Covers simple-reply call sequence, overflow→sendDocument, and
two-frontends-observe-same-session."
```

---

## Task 18: Version bump + release

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump version**

In `pyproject.toml`: `version = "0.10.0"` → `version = "0.11.0"`.

- [ ] **Step 2: Update `CHANGELOG.md`**

Prepend:

```markdown
## v0.11.0 — 2026-05-26

### Telegram renderer + correctness (buckets B+D from the v0.10 critique)

- Replace MarkdownV2-escape-everything render path with HTML parse mode.
  Worker replies with fenced code, bold, italic, blockquotes, links now
  render natively instead of as literal backslashes.
- Greedy chunker; replies >3 parts spill to a `.md` attachment with a
  500-char peek caption (uses new `sendDocument`).
- Status message becomes a live per-turn ticker — edits on tool-use
  boundaries instead of every 2s. Tool-call activity is now visible.
- Multi-observer migration: TUI and Telegram both register via
  `add_event_observer` / `add_state_observer`; two frontends can
  observe the same session without clobbering.
- New `add_close_observer` on `AgentSession`; `_active` clears on any
  session-close path.
- Telegram update offset persists across restart.
- Tactical fixes: send_message=None guard, refresh-loop exceptions
  caught and logged.

### New dependency
- `markdown-it-py>=3.0`
```

- [ ] **Step 3: Run full test suite + lint**

```bash
uv run ruff check src tests
uv run pytest -m "not live" -v
```

All must pass.

- [ ] **Step 4: Commit + tag + push**

```bash
git add pyproject.toml CHANGELOG.md uv.lock
git commit -m "release: 0.11.0 — telegram renderer + correctness (B+D)"
git push origin main
git tag v0.11.0
git push origin v0.11.0
```

The release workflow (`.github/workflows/release.yml`) handles PyPI publish on tag push.

- [ ] **Step 5: Manual verification on zion**

After the wheel lands on PyPI:

1. `uv sync` (or whatever pulls the new wheel into the local serve).
2. Restart `aegis serve`.
3. Send a worker reply with intentional markdown — fenced code, bold, link. Verify renders natively.
4. Trigger a long turn (5+ tool calls, ≥2 min). Verify ticker updates live; no freeze.
5. Trigger a ~30KB reply. Verify `.md` attachment lands with peek caption.
6. Restart serve mid-conversation. Verify no replay of pre-restart commands.
7. Run TUI + Telegram against the same session. Verify both see events.
8. Trigger a `magpie → warden` handoff. Verify ticker shows envelope.

- [ ] **Step 6: Journal**

```bash
echo "> 🤖 $(date +%H:%M) — milestone: v0.11.0 released (telegram renderer+correctness, B+D)" \
  >> /home/apiad/Workspace/vault/Calendar/Journal/journal-$(date +%Y-%m-%d).md
```

---

## Self-review checklist

After completing all tasks, verify:

- [ ] Every spec requirement is covered by at least one task (the renderer; the chunker w/ Spillover; `sendDocument`; the ticker; envelope detection via `on_inbox`; observer migration; `on_close`; `_active` cleanup; offset persistence; `send_message=None` guard; refresh-loop exception catch; dep add).
- [ ] No `markdown=True` callers remain (`grep -rn 'markdown=True' src/`).
- [ ] No `on_event\s*=` or `on_state\s*=` assignments to a core/session outside `session.py` itself (`grep -rn 'on_event\s*=\|on_state\s*=' src/ | grep -v session.py`).
- [ ] `_refresh_loop` is gone (`grep -rn '_refresh_loop' src/`).
- [ ] `escape_md` is gone (`grep -rn 'escape_md' src/`).
- [ ] Version is `0.11.0` in `pyproject.toml`.
- [ ] Tag `v0.11.0` exists locally and on origin.
