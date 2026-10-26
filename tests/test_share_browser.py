"""Links and shares that open a New session form, filled and unsent (#735)."""

import re
from urllib.parse import quote

import pytest

from .test_browser import (  # noqa: F401
    PNG,
    browser,
    linked,
    page,
    server,
    turns_done,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def origin(server) -> str:  # noqa: F811
    return re.match(r"https?://[^/]+", server.url).group(0)


def link(srv, **q) -> str:
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in q.items())
    return f"{origin(srv)}/#new?{query}"


def wait_filled(pg, cwd: str) -> None:
    pg.wait_for_selector("#a2[data-view=spawn]")
    pg.wait_for_function(
        "(cwd) => document.querySelector('#sp-agent').value !== ''"
        " && document.querySelector('#sp-cwd').value === cwd",
        arg=cwd,
    )


def test_a_link_fills_the_open_page_and_sends_nothing(server, page, tmp_path):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    prompt = "fix the build\n& ship it + test 100%"
    page.goto(link(server, prompt=prompt, cwd=str(tmp_path)))
    wait_filled(page, str(tmp_path))
    assert page.input_value("#sp-text") == prompt
    # Read once: the query is gone from the URL, so a reload fills nothing more.
    assert page.evaluate("location.hash") == "#new"
    page.wait_for_timeout(500)
    assert page.locator("#tablist .tab[data-id]").count() == 0
    page.reload()
    page.wait_for_selector("#a2[data-view=spawn]")
    assert page.input_value("#sp-text").count("fix the build") <= 1
    assert page.errors == []


def test_a_link_boots_a_closed_app_into_the_filled_form(server, browser, tmp_path):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    first = ctx.new_page()
    first.goto(server.url)  # signs the context in, as an installed app is
    first.wait_for_selector("#a2[data-view=fleet]")
    first.close()
    fresh = ctx.new_page()
    errors: list = []
    fresh.on("pageerror", lambda e: errors.append(str(e)))
    fresh.goto(link(server, prompt="from a chat", cwd=str(tmp_path)))
    wait_filled(fresh, str(tmp_path))
    assert fresh.input_value("#sp-text") == "from a chat"
    assert fresh.locator("#tablist .tab[data-id]").count() == 0
    assert errors == []
    ctx.close()


def test_a_link_picks_a_linked_server_and_its_folder(linked, browser):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    pg.goto(linked.alpha.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    cwd = str(linked.beta.root)
    pg.goto(link(linked.alpha, prompt="born on beta", server="beta", cwd=cwd))
    wait_filled(pg, cwd)
    assert pg.evaluate("document.querySelector('#sp-server').value") == "beta"
    assert pg.input_value("#sp-text") == "born on beta"
    ctx.close()


def test_a_link_to_a_server_not_linked_says_so(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.goto(link(server, prompt="over there", server="nowhere", cwd="/elsewhere"))
    page.wait_for_selector(".toast.error")
    assert "nowhere is not a linked server" in page.inner_text(".toast.error")
    assert page.input_value("#sp-text") == "over there"
    assert page.input_value("#sp-cwd") != "/elsewhere"
    assert page.errors == []


def test_a_share_lands_unsent_with_its_files_and_sends_them(server, page, tmp_path):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.evaluate("navigator.serviceWorker.ready")
    # What Android posts to the manifest's share_target, sent by a form.
    page.evaluate("""() => {
      const f = Object.assign(document.createElement('form'),
        { method: 'post', enctype: 'multipart/form-data', action: '/share' });
      const field = (name, value) => f.append(Object.assign(document.createElement('input'), { name, value }));
      field('title', 'Release notes');
      field('text', 'see https://example.com/r');
      field('url', 'https://example.com/r');
      f.append(Object.assign(document.createElement('input'), { type: 'file', name: 'files', multiple: true, id: 'shared' }));
      document.body.append(f);
    }""")
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)
    notes = tmp_path / "notes.txt"
    notes.write_text("hello\n")
    page.set_input_files("#shared", [str(shot), str(notes)])
    page.evaluate("document.querySelector('#shared').form.submit()")
    page.wait_for_selector("#a2[data-view=spawn]")
    page.wait_for_function(
        "document.querySelectorAll('#sp-atts .chip.att').length === 2"
    )
    # The title kept, the link once: Android puts it inside the text too.
    assert page.input_value("#sp-text") == "Release notes\nsee https://example.com/r"
    assert page.evaluate("location.hash") == "#new"
    assert page.evaluate("caches.has('aegis.share')") is False
    assert page.locator("#tablist .tab[data-id]").count() == 0
    page.press("#sp-text", "Enter")
    page.wait_for_selector("#a2[data-view=session]")
    turns_done(page, 1)
    row = page.locator(".row.user").last
    assert row.locator(".fcard .count").inner_text() == "1 / 2"
    assert "Release notes" in row.inner_text()
    assert page.errors == []
