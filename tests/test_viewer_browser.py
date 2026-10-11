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
