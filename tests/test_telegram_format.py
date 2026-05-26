from aegis.telegram.format import Spillover, chunk, status_line


def test_status_line_shape():
    s = status_line("lucid-knuth", "working", "·opus·high", "↑0 (–) ↓0")
    assert s == "⏳ lucid-knuth · working · ·opus·high ↑0 (–) ↓0"


def test_spillover_carries_raw_md_and_rendered_html():
    s = Spillover(raw_md="# title\n\nbody", rendered_html="<b>title</b>\n\nbody")
    assert s.raw_md == "# title\n\nbody"
    assert s.rendered_html == "<b>title</b>\n\nbody"


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
    assert len(out) == 1
    assert pre in out[0]
