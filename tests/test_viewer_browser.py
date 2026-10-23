"""The viewer: a card's Open shows the file or the page whole over the
session's column, leaving the tabs and the session panel in reach (#313).

Kept out of test_browser.py, whose server, page and helpers it borrows."""

import json

import pytest

from .test_browser import (  # noqa: F401
    PICK,
    PNG,
    _artifact,
    browser,
    page,
    server,
    spawn,
    turns_done,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def send(pg, n, paths):
    pg.fill("#input", f"/mcp file_send {json.dumps({'paths': paths})}")
    pg.press("#input", "Enter")
    turns_done(pg, n)


def test_open_shows_the_whole_file_over_the_transcript_until_closed(
    server,  # noqa: F811
    page,  # noqa: F811
):
    # Both longer than the card's 40-line excerpt, so the viewer fetched them.
    tail = "".join(f"line {i}\n\n" for i in range(1, 61))
    (server.root / "notes.md").write_text(
        f"# Notes\n\n```python\ndef f():\n    return 1\n```\n\n{tail}"
    )
    (server.root / "code.py").write_text(
        "".join(f"def f{i}():\n    return {i}\n" for i in range(30))
    )
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    send(page, 1, ["notes.md", "code.py"])

    # The card's actions are icons that name themselves.
    acts = page.locator(".row.file .fbar .acts .btn:visible")
    assert [acts.nth(i).get_attribute("title") for i in range(acts.count())] == [
        "Open",
        "Dock beside the transcript",
        "Open in a new tab",
        "Download",
    ]
    assert all(acts.nth(i).inner_text().strip() == "" for i in range(acts.count()))

    viewer = page.locator("#viewer")
    assert viewer.is_hidden()
    page.click(".row.file .fbar .view")
    viewer.wait_for()
    doc = viewer.locator(".vbody .doc")
    doc.locator("text=line 60").wait_for()
    assert doc.locator("h1").inner_text() == "Notes"
    assert doc.locator("pre code .hljs-keyword").first.inner_text() == "def"
    assert viewer.locator(".vbar .fn").inner_text() == "notes.md"
    # It covers the transcript and the composer, not the tabs or the panel.
    assert page.locator("#input").is_hidden() and page.locator("#entries").is_hidden()
    assert page.locator("#side").is_visible() and page.locator("#tab-add").is_visible()
    box, col = viewer.bounding_box(), page.locator(".v-session .col").bounding_box()
    assert box == col

    viewer.locator(".vbar .pager .btn").nth(1).click()
    assert viewer.locator(".vbar .fn").inner_text() == "code.py"
    code = viewer.locator(".code")
    code.locator(".hljs-keyword").first.wait_for()
    assert code.locator(".ln").inner_text().split()[-1] == "60"
    assert "return 29" in code.locator(".src").inner_text()

    # Another tab shows its own column; the way back finds the viewer as it was.
    other = spawn(page)
    assert viewer.is_hidden() and page.locator("#input").is_visible()
    page.evaluate(f"location.hash = '#s={sid}'")
    page.wait_for_function(f"location.hash === '#s={sid}'")
    viewer.locator(".code .hljs-keyword").first.wait_for()
    assert viewer.locator(".vbar .fn").inner_text() == "code.py"
    assert other != sid

    page.keyboard.press("Escape")
    assert viewer.is_hidden()
    assert page.locator("#input").is_visible() and page.locator("#entries").is_visible()
    assert page.errors == []


def test_an_image_opens_in_the_viewer_from_its_preview(
    server,  # noqa: F811
    page,  # noqa: F811
):
    (server.root / "dot.png").write_bytes(PNG)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    send(page, 1, ["dot.png"])
    page.click(".row.file .stage img")
    img = page.locator("#viewer .vbody img")
    img.wait_for()
    page.wait_for_function(
        "document.querySelector('#viewer .vbody img').naturalWidth > 0"
    )
    page.click("#viewer .vclose")
    assert page.locator("#viewer").is_hidden()
    assert page.errors == []


def test_a_live_page_runs_only_in_the_viewer_and_answers_from_there(
    server,  # noqa: F811
    page,  # noqa: F811
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    page.frame_locator(f".row.artifact iframe[data-artifact={aid}]").locator(
        "#st"
    ).filter(has_text="{}").wait_for()

    page.click(".row.artifact .fbar .view")
    inner = page.frame_locator(f"#viewer iframe[data-artifact={aid}]")
    inner.locator("#st").filter(has_text="{}").wait_for()
    assert page.locator("iframe[data-artifact]").count() == 1, (
        "one live copy of the page"
    )
    assert page.locator(".row.artifact .acard .held").count() == 1
    # The page fills the viewer rather than asking for a height.
    frame = page.locator("#viewer iframe").bounding_box()
    body = page.locator("#viewer .vbody").bounding_box()
    assert frame["height"] == body["height"]

    inner.locator("#b").click()
    # It lands under the viewer, which still covers the transcript.
    page.wait_for_selector(
        f".row.inbox .from >> text=artifact:{aid} · submit",
        state="attached",
        timeout=10000,
    )
    page.locator("#viewer iframe[data-status=submitted]").wait_for()
    page.keyboard.press("Escape")
    page.wait_for_selector(
        ".row.artifact .acard[data-status=submitted] .done >> text=Picked B"
    )
    assert page.locator(".row.artifact .acard .held").count() == 0
    assert page.errors == []


def test_closing_the_viewer_gives_a_live_page_back_to_its_card(
    server,  # noqa: F811
    page,  # noqa: F811
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    page.click(".row.artifact .fbar .view")
    page.frame_locator(f"#viewer iframe[data-artifact={aid}]").locator("#st").filter(
        has_text="{}"
    ).wait_for()
    page.click("#viewer .vclose")
    card = page.frame_locator(f".row.artifact iframe[data-artifact={aid}]")
    card.locator("#st").filter(has_text="{}").wait_for()
    assert page.locator(".row.artifact .acard .held").count() == 0
    assert page.errors == []


def test_the_viewer_colours_through_the_loader_and_fetches_one_language(
    server,  # noqa: F811
    page,  # noqa: F811
):
    """The viewer loads the highlighter's core and Python, not the old bundle
    (#323)."""
    (server.root / "code.py").write_text("def f():\n    return 1\n")
    fetched: list[str] = []
    page.on(
        "request",
        lambda r: (
            "/vendor/" in r.url
            and fetched.append(
                r.url.rsplit("/", 2)[-2] + "/" + r.url.rsplit("/", 1)[-1]
            )
        ),
    )
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    send(page, 1, ["code.py"])
    page.click(".row.file .fbar .view")
    page.locator("#viewer .code .hljs-keyword").first.wait_for()
    assert "hljs/core.min.js" in fetched and "languages/python.min.js" in fetched
    assert not any(u.endswith("highlight.mjs") for u in fetched)
    colour = page.evaluate(
        "getComputedStyle(document.querySelector('#viewer .hljs-keyword')).color"
    )
    assert colour == "rgb(201, 162, 122)", "ink's quiet --h-kw, #c9a27a"
    assert page.errors == []


def test_a_fence_in_the_viewer_copies_its_code(
    server,  # noqa: F811
    browser,  # noqa: F811
):
    """The copy listener sits on the viewer too, not only on the transcript
    (#323)."""
    (server.root / "snip.md").write_text("# Snip\n\n```python\nprint('hi')\n```\n")
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        permissions=["clipboard-read", "clipboard-write"],
    )
    pg = ctx.new_page()
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    try:
        pg.goto(server.url)
        pg.wait_for_selector("#a2[data-view=fleet]")
        spawn(pg)
        send(pg, 1, ["snip.md"])
        pg.click(".row.file .fbar .view")
        fence = pg.locator("#viewer .vbody .doc .fence")
        fence.wait_for()
        button = fence.locator(".copy")
        pg.evaluate("navigator.clipboard.writeText('')")
        fence.hover()
        button.click()
        pg.wait_for_function(
            "b => b.dataset.state === 'done' || b.dataset.state === 'err'",
            arg=button.element_handle(),
            timeout=3000,
        )
        assert button.get_attribute("data-state") == "done", button.get_attribute(
            "title"
        )
        assert pg.evaluate("navigator.clipboard.readText()") == "print('hi')\n"
        assert errors == []
    finally:
        ctx.close()


def test_a_refused_submit_in_the_viewer_says_why_in_view(
    server,  # noqa: F811
    page,  # noqa: F811
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    # A label over 140 characters is refused by the server; the page posts it
    # raw, as one that skips the script's checks would. A page must call
    # aegis.submit somewhere to be sent at all.
    bad = (
        '<button id="bad">bad</button><button id="ok">ok</button><span id="st"></span>',
        "st.textContent = JSON.stringify(state);"
        '  ok.onclick = () => aegis.submit({pick: "b"}, "Picked B");'
        '  bad.onclick = () => parent.postMessage({jsonrpc: "2.0", id: 99, method: "aegis/submit",'
        '    params: {data: {pick: "b"}, label: "x".repeat(200)}}, "*");',
    )
    aid, _ = _artifact(page, server, bad, 0)
    page.frame_locator(f".row.artifact iframe[data-artifact={aid}]").locator(
        "#st"
    ).filter(has_text="{}").wait_for()
    page.click(".row.artifact .fbar .view")
    inner = page.frame_locator(f"#viewer iframe[data-artifact={aid}]")
    inner.locator("#st").filter(has_text="{}").wait_for()
    inner.locator("#bad").click()
    note = page.locator("#viewer .vbody .refused")
    note.wait_for()
    assert note.inner_text().startswith("Not sent: ")
    # In view, not under the frame that fills the viewer.
    box, body = note.bounding_box(), page.locator("#viewer .vbody").bounding_box()
    assert box["y"] + box["height"] <= body["y"] + body["height"] + 1, (box, body)
    assert page.errors == []


def test_a_swipe_pages_the_card_and_the_viewer_round_the_set(
    server,  # noqa: F811
    browser,  # noqa: F811
    page,  # noqa: F811
):
    for name in ("a.md", "b.md", "c.md"):
        (server.root / name).write_text(f"# {name}\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page)
    send(page, 1, ["a.md", "b.md", "c.md"])
    page.close()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    phone = ctx.new_page()
    phone.errors = []
    phone.on("pageerror", lambda e: phone.errors.append(str(e)))
    phone.goto(f"{server.url}#s={key}")
    cdp = ctx.new_cdp_session(phone)

    def drag(sel, dx, dy=0):
        box = phone.locator(sel).bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        for kind, px, py in (
            ("touchStart", x, y),
            ("touchMove", x + dx / 2, y + dy / 2),
            ("touchMove", x + dx, y + dy),
        ):
            cdp.send(
                "Input.dispatchTouchEvent",
                {"type": kind, "touchPoints": [{"x": px, "y": py}]},
            )
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    card = ".row.file .stage"
    fn = phone.locator(".row.file .fbar .fn")
    phone.locator(card).wait_for()
    drag(card, -150)  # finger to the left: the next file
    assert fn.inner_text() == "b.md"
    drag(card, 150)
    drag(card, 150)  # back past the first file: the last one
    assert fn.inner_text() == "c.md"
    drag(card, -150)
    assert fn.inner_text() == "a.md"
    drag(card, -20)  # too short to be a swipe
    drag(card, -80, 120)  # mostly down: a scroll
    assert fn.inner_text() == "a.md"

    phone.click(".row.file .fbar .view")
    vfn = phone.locator("#viewer .vbar .fn")
    phone.locator("#viewer .vbody .doc").wait_for()
    drag("#viewer .vbody", 150)
    assert vfn.inner_text() == "c.md"
    drag("#viewer .vbody", -150)
    assert vfn.inner_text() == "a.md"
    assert phone.errors == []
    ctx.close()
