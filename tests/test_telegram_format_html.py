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
