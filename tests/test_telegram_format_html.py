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
