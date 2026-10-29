"""Tool rows drawn for what they hold: a numbered, coloured file view, a
coloured command, and an edit side by side or in one column (#323)."""

import pytest

from .test_browser import EXPECTED_CONSOLE, browser, server, spawn, turns_done  # noqa: F401
from .test_markdown_browser import make_page  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def open_tool(page, n: int = 0):
    tool = page.locator(".row.tool").nth(n)
    tool.locator("summary").click()
    return tool


def test_a_read_shows_numbered_coloured_lines(server, make_page, tmp_path):  # noqa: F811
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, f"/readn {tmp_path / 'a.py'}")
    tool = open_tool(page)
    tool.locator(".lines .hljs-keyword").first.wait_for()
    assert tool.locator(".lines .n").all_inner_texts() == ["1", "2"]
    assert tool.locator("pre.out").count() == 0
    assert page.errors == []


def test_a_bash_command_is_coloured(server, make_page):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, "/bash echo hi")
    tool = open_tool(page)
    tool.locator(".md.args .fence code.hljs").wait_for()
    assert page.errors == []


def edit(page, tmp_path):
    path = tmp_path / "e.py"
    path.write_text("".join(f"v{i} = {i}\n" for i in range(20)))
    spawn(page, f"/edit {path} v10 = 10 => v10 = cache_key(10)")
    return open_tool(page)


@pytest.mark.quarantine("#544")
def test_an_edit_is_side_by_side_on_a_wide_row(server, make_page, tmp_path):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    tool = edit(page, tmp_path)
    split = tool.locator(".diff .split").first
    split.wait_for()
    assert tool.locator(".diff .unified").first.is_hidden()
    assert "cache_key" in split.locator(".c.add mark").all_inner_texts()[0]
    assert page.errors == []


def test_an_edit_is_one_column_on_a_phone_and_switches_on_resize(
    server,  # noqa: F811
    make_page,  # noqa: F811
    tmp_path,
):
    page = make_page(390)
    page.goto(server.url)
    tool = edit(page, tmp_path)
    uni = tool.locator(".diff .unified").first
    uni.wait_for()
    assert tool.locator(".diff .split").first.is_hidden()
    assert uni.locator(".s.del").count() == 1 and uni.locator(".s.add").count() == 1
    page.set_viewport_size({"width": 1280, "height": 900})
    tool.locator(".diff .split").first.wait_for()
    assert page.errors == []


def test_a_write_shows_the_new_file(server, make_page, tmp_path):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, f"/write {tmp_path / 'w.py'} => import os")
    tool = open_tool(page)
    tool.locator(".lines").wait_for()
    assert tool.locator(".diff").count() == 0
    assert page.errors == []
