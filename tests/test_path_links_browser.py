"""A file path in a reply opens the viewer at the line it names (#323)."""

import pytest

from .test_browser import (  # noqa: F401
    EXPECTED_CONSOLE,
    browser,
    linked,
    page,
    pick,
    server,
    spawn,
    turns_done,
)  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def test_a_path_with_a_line_opens_the_viewer_there(server, page):  # noqa: F811
    lines = "".join(f"line_{n} = {n}\n" for n in range(1, 301))
    (server.root / "src").mkdir()
    (server.root / "src" / "long.py").write_text(lines)
    page.goto(server.url)
    spawn(page, "/md The bug is in `src/long.py:142`.")
    link = page.locator(".row.prose code.path").first
    assert link.get_attribute("data-index") == "0"
    link.click()
    mark = page.locator("#viewer .code .mark")
    mark.wait_for()
    assert mark.get_attribute("data-line") == "142"
    box, view = mark.bounding_box(), page.locator("#viewer .vbody").bounding_box()
    assert view["y"] <= box["y"] <= view["y"] + view["height"], "line 142 is in view"
    number = page.evaluate(
        """() => {
          const t = document.querySelector('#viewer .code .ln').firstChild;
          const i = t.data.indexOf('\\n142\\n') + 1, r = document.createRange();
          r.setStart(t, i);
          r.setEnd(t, i + 3);
          const b = r.getBoundingClientRect();
          return b.top + b.height / 2;
        }"""
    )
    assert box["y"] <= number <= box["y"] + box["height"], "the mark covers line 142"
    assert page.errors == []


def test_a_line_past_the_end_opens_at_the_last_line(server, page):  # noqa: F811
    (server.root / "src").mkdir()
    (server.root / "src" / "short.py").write_text("a = 1\nb = 2\n")
    page.goto(server.url)
    spawn(page, "/md See `src/short.py:90`.")
    page.locator(".row.prose code.path").first.click()
    page.locator("#viewer .code .mark").wait_for()
    assert page.locator("#viewer .code .mark").get_attribute("data-line") == "2"
    assert page.errors == []


def test_a_missing_file_says_where_it_looked(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, "/md See `gone/none.py`.")
    page.locator(".row.prose code.path").first.click()
    note = page.locator(".row.prose .patherr")
    note.wait_for()
    assert "no file gone/none.py" in note.inner_text()
    assert page.locator("#viewer").is_hidden()
    assert page.errors == []


def test_a_path_in_both_the_root_and_the_cwd_asks_which_to_open(server, page):  # noqa: F811
    (server.root / "docs").mkdir()
    (server.root / "docs" / "x.md").write_text("# Root\n")
    (server.root / "sub" / "docs").mkdir(parents=True)
    (server.root / "sub" / "docs" / "x.md").write_text("# Under the cwd\n")
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    page.fill("#sp-cwd", "sub")
    page.fill("#sp-text", "/md See `docs/x.md`.")
    page.click("#sp-go")
    page.wait_for_selector("#a2[data-view=session]")
    turns_done(page, 1)
    page.locator(".row.prose code.path").first.click()
    note = page.locator(".row.prose .patherr")
    note.wait_for()
    assert "docs/x.md names two files" in note.inner_text()
    buttons = note.locator("button.btn")
    assert buttons.all_inner_texts() == ["Open the root's", "Open the session folder's"]
    assert page.locator("#viewer").is_hidden()
    buttons.nth(1).click()
    page.locator("#viewer").wait_for()
    page.wait_for_function(
        "document.querySelector('#viewer .vbody').textContent.includes('Under the cwd')"
    )
    assert "Root" not in page.locator("#viewer .vbody").inner_text()
    assert page.errors == []


def test_a_linked_servers_reply_draws_no_path_link(linked, page):  # noqa: F811
    (linked.beta.root / "src").mkdir()
    (linked.beta.root / "src" / "far.py").write_text("x = 1\n")
    page.goto(linked.alpha.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.click("#tab-add")
    page.wait_for_selector("#sp-server:not([hidden])")
    pick(page, "#sp-server", "beta")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    page.fill("#sp-text", "/md See `src/far.py:1`.")
    page.click("#sp-go")
    page.wait_for_selector("#a2[data-view=session]")
    turns_done(page, 1)
    code = page.locator(".row.prose .md code")
    assert code.first.inner_text() == "src/far.py:1", "the span is drawn"
    assert page.locator(".row.prose code.path").count() == 0, (
        "the file is on the other machine"
    )
    assert page.errors == []
