"""Rules over the client's source that a test can hold without a browser."""

import re
from pathlib import Path

JS = Path(__file__).parents[1] / "src" / "aegis" / "client" / "js"
VENDOR = JS.parent / "vendor" / "hljs"


def test_the_client_never_opens_a_native_dialog():
    """A native confirm, alert or prompt looks foreign in the installed app and
    blocks the page; the client asks through js/dialog.js."""
    found = [
        f"{p.name}:{n}"
        for p in JS.glob("*.js")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"(?:^|[^\w.]|window\.)(confirm|alert|prompt)\(", line)
    ]
    assert found == []


def test_only_keys_js_listens_for_keys_on_the_page():
    """A page-wide key binding lives in the registry in js/keys.js, so the ?
    list and the command palette show it (#248). A widget may answer keys in
    its own field, as the command menu and the palette do, but a listener on
    the document or the window anywhere else is a key nobody can find.
    pagepoint.js runs inside a sandboxed page, not this one: its Esc only
    closes the menu the host drew over that page (#479)."""
    found = [
        f"{p.name}:{n}"
        for p in JS.glob("*.js")
        if p.name not in ("keys.js", "pagepoint.js")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(
            r"""(document|window|self)\.addEventListener\(\s*["']key(down|up|press)""",
            line,
        )
    ]
    assert found == []


def test_the_client_draws_no_native_select():
    """A native select pops up in the operating system's chrome, outside the
    theme, and answers typing by jumping to the next option starting with that
    letter; the client's chips are <pick-chip> (js/pick.js)."""
    files = [*JS.glob("*.js"), JS.parent / "index.html"]
    found = [
        f"{p.name}:{n}"
        for p in files
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r'<select\b|<datalist\b|\("select"|\("datalist"', line)
    ]
    assert found == []


def test_the_loader_lists_exactly_the_vendored_languages():
    """js/highlight.js imports a language by name from vendor/hljs/languages;
    a name without a file fails at runtime, a file without a name is dead
    weight in the wheel (#323)."""
    src = (JS / "highlight.js").read_text()
    listed = re.search(r"export const LANGS = \[([^\]]*)\]", src)
    assert listed, "LANGS is a one-line array literal"
    names = set(re.findall(r'"([a-z0-9+-]+)"', listed.group(1)))
    files = {
        p.name.removesuffix(".min.js") for p in (VENDOR / "languages").glob("*.min.js")
    }
    assert names == files
    assert (VENDOR / "core.min.js").is_file()


def test_the_loader_registers_what_a_grammar_embeds():
    """highlight.js colours a grammar's subLanguage only when it is registered,
    and plain text otherwise, with no error; EMBEDS in js/highlight.js names
    every vendored one a vendored grammar uses, and GUESS only vendored names
    (#323)."""
    src = (JS / "highlight.js").read_text()
    langs = set(
        re.findall(
            r'"([a-z0-9+-]+)"',
            re.search(r"export const LANGS = \[([^\]]*)\]", src).group(1),
        )
    )
    listed = re.search(r"const EMBEDS = \{(.*)\};", src)
    assert listed, "EMBEDS is a one-line object literal"
    embeds = {
        k.strip('"'): set(re.findall(r'"([a-z0-9+-]+)"', v))
        for k, v in re.findall(
            r'("[a-z0-9+-]+"|[a-z0-9]+): \[([^\]]*)\]', listed.group(1)
        )
    }
    missing = []
    for p in sorted((VENDOR / "languages").glob("*.min.js")):
        name = p.name.removesuffix(".min.js")
        used = set()
        for one, many in re.findall(
            r'subLanguage:(?:"([^"]+)"|\[([^\]]*)\])', p.read_text()
        ):
            used |= {one} if one else set(re.findall(r'"([^"]+)"', many))
        missing += [
            f"{name}: {u}" for u in sorted((used & langs) - embeds.get(name, set()))
        ]
    assert missing == []
    guess = re.search(r"const GUESS = \[([^\]]*)\]", src)
    assert set(re.findall(r'"([a-z0-9+-]+)"', guess.group(1))) <= langs


def test_every_language_python_names_is_vendored():
    """describe.tool_lang names a language the browser must be able to load."""
    from aegis.transcript import describe

    src = (JS / "highlight.js").read_text()
    listed = re.search(r"export const LANGS = \[([^\]]*)\]", src).group(1)
    names = set(re.findall(r'"([a-z0-9+-]+)"', listed))
    used = {
        *describe.LANG_BY_EXT.values(),
        *describe.LANG_BY_NAME.values(),
        "bash",
        "yaml",
    }
    assert used <= names


THEMES = JS.parent / "themes"
FONTS = JS.parent / "fonts"


def theme_names() -> list[str]:
    block = re.search(r"const THEMES = \[(.*?)\];", (JS / "app.js").read_text(), re.S)
    return re.findall(r'value: "([a-z]+)"', block.group(1))


def test_every_theme_in_settings_has_a_stylesheet_the_page_links():
    """A theme Settings offers without its file, or with a file index.html never
    links, paints the page in base.css's undefined variables."""
    names = theme_names()
    html = (JS.parent / "index.html").read_text()
    assert sorted(names) == sorted(p.stem for p in THEMES.glob("*.css"))
    assert [n for n in names if f'href="/static/themes/{n}.css"' not in html] == []
    for n in names:
        assert f"[data-theme={n}] #a2{{" in (THEMES / f"{n}.css").read_text()


def test_every_font_a_theme_names_is_vendored():
    """A family with no @font-face falls back to whatever the machine has, so
    the theme looks different on every laptop."""
    fonts_css = (JS.parent / "css" / "fonts.css").read_text()
    faces = set(re.findall(r"font-family:'([^']+)'", fonts_css))
    for src in re.findall(r"url\('/static/fonts/([^']+)'\)", fonts_css):
        assert (FONTS / src).is_file(), src
    named = {
        f
        for p in THEMES.glob("*.css")
        for f in re.findall(r"--font-[a-z]+:'([^']+)'", p.read_text())
    }
    assert named - faces == set()


CODEMIRROR = JS.parent / "vendor" / "codemirror"


def test_the_editor_lists_exactly_the_vendored_languages():
    """js/editor.js imports lang-<name>.mjs by name from vendor/codemirror; a
    name without a file fails at runtime, a file without a name is dead weight
    in the wheel, and an extension mapped to an unlisted name never colours."""
    src = (JS / "editor.js").read_text()
    listed = re.search(r"export const LANGS = \[([^\]]*)\]", src)
    assert listed, "LANGS is a one-line array literal"
    names = set(re.findall(r'"([a-z0-9+-]+)"', listed.group(1)))
    files = {
        p.name.removeprefix("lang-").removesuffix(".mjs")
        for p in CODEMIRROR.glob("lang-*.mjs")
    }
    assert names == files
    assert (CODEMIRROR / "core.mjs").is_file()
    mapped = re.search(r"const BY_EXT = \{(.*?)\};", src, re.S).group(1)
    mapped += re.search(r"const BY_NAME = \{(.*?)\};", src, re.S).group(1)
    assert set(re.findall(r':\s*"([a-z0-9+-]+)"', mapped)) <= names
