from aegis.telegram.format import chunk, escape_md, status_line

# Telegram MarkdownV2 reserved chars (Bot API spec): must always be
# preceded by `\` when appearing outside of an entity.
_MDV2_RESERVED = set(r"_*[]()~`>#+-=|{}.!") | {"\\"}


def _mdv2_violations(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch in _MDV2_RESERVED:
            out.append((i, ch))
        i += 1
    return out


def test_escape_md():
    assert escape_md("a_b*c[") == "a\\_b\\*c\\["


def test_escape_md_escapes_backslash():
    # Telegram MarkdownV2 lists `\` itself among the chars that must
    # always be escaped. A literal `\b` would otherwise be eaten by
    # the parser as a (nonexistent) escape.
    assert escape_md(r"a\b") == r"a\\b"


def test_chunk_caps_and_labels():
    parts = chunk("x" * 9000, label="lucid-knuth", limit=4096, max_parts=2)
    assert len(parts) == 2
    # The label and `(N/M)` framing carry MarkdownV2 reserved chars
    # (`-`, `(`, `)`) and must be escaped or Telegram rejects the
    # whole message with 400 "can't parse entities".
    assert parts[0].startswith(r"lucid\-knuth \(1/2\)")
    assert "truncated" in parts[-1]


def test_chunk_single_no_label_noise():
    parts = chunk("short", label="h", limit=4096, max_parts=5)
    assert parts == ["short"]


def test_chunk_multipart_is_valid_markdownv2():
    # Regression for the silent-drop bug: long replies were sent with
    # `parse_mode=MarkdownV2` but the per-part label header carried
    # unescaped `-`, `(`, `)`. Telegram rejected every chunk and the
    # error was swallowed by the bot client. Every part returned by
    # chunk() must now be legal MarkdownV2.
    body = "Here is the answer. " * 250  # ~5000 chars, forces multi-part
    parts = chunk(body, label="lucid-knuth", limit=4096, max_parts=2)
    assert len(parts) >= 2
    for p in parts:
        assert _mdv2_violations(p) == [], (
            f"unescaped reserved chars: {_mdv2_violations(p)[:5]} "
            f"in part starting {p[:80]!r}"
        )


def test_chunk_truncation_suffix_is_valid_markdownv2():
    # The `… (truncated, N more chunks)` tail uses `(`, `,`, `.`, `)`,
    # all reserved. Force a drop and validate.
    body = "x" * 200_000
    parts = chunk(body, label="lucid-knuth", limit=4096, max_parts=2)
    assert "truncated" in parts[-1]
    assert _mdv2_violations(parts[-1]) == []


def test_chunk_does_not_split_escape_pair():
    # If the slice boundary lands between a `\` and the reserved char
    # it escapes, part N ends with a dangling `\` and part N+1 starts
    # with an unescaped reserved char. Heavy-punctuation body forces
    # max density of `\X` pairs to maximise the chance of hitting it.
    body = ". " * 3000  # every `.` becomes `\.` after escaping
    parts = chunk(body, label="h", limit=4096, max_parts=5)
    assert len(parts) >= 2
    for p in parts:
        assert _mdv2_violations(p) == [], (
            f"split escape pair near boundary in {p[-40:]!r}")
        assert not p.endswith("\\"), (
            f"part ends with dangling backslash: {p[-20:]!r}")


def test_status_line_shape():
    s = status_line("lucid-knuth", "working", "·opus·high", "↑0 (–) ↓0")
    assert s == "⏳ lucid-knuth · working · ·opus·high ↑0 (–) ↓0"
