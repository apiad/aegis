from aegis.telegram.format import chunk, escape_md, status_line


def test_escape_md():
    assert escape_md("a_b*c[") == "a\\_b\\*c\\["


def test_chunk_caps_and_labels():
    parts = chunk("x" * 9000, label="lucid-knuth", limit=4096, max_parts=2)
    assert len(parts) == 2
    assert parts[0].startswith("lucid-knuth (1/2)")
    assert "truncated" in parts[-1]


def test_chunk_single_no_label_noise():
    parts = chunk("short", label="h", limit=4096, max_parts=5)
    assert parts == ["short"]


def test_status_line_shape():
    s = status_line("lucid-knuth", "working", "·opus·high", "↑0 (–) ↓0")
    assert s == "⏳ lucid-knuth · working · ·opus·high ↑0 (–) ↓0"
