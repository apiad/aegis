"""The Files view (#458) as a person uses it: Alt+O, the editor, saving, and a
file an agent changed under the buffer. Kept out of test_browser.py, whose
server fixture it borrows."""

import os

import pytest

from .test_browser import browser, page, server, spawn  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]

EDITOR = ".v-files .cm-content"
BUFFER = "[...document.querySelectorAll('.v-files .cm-line')].map(l => l.textContent).join('\\n')"


def write(path, text: str, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))


def open_file(pg, srv, rel: str) -> None:
    pg.goto(f"{srv.url.split('#')[0]}#files={rel}")
    pg.wait_for_selector(EDITOR)


def test_alt_o_from_a_session_opens_the_newest_file_and_ctrl_s_saves(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    write(tmp_path / "src" / "old.py", "old = 1\n", 1_000_000_000)
    write(tmp_path / "src" / "app.py", "x = 1\n", 2_000_000_000)
    page.goto(server.url)
    spawn(page)

    page.keyboard.press("Alt+o")
    page.wait_for_selector("#fpick:not([hidden]) .file-row")
    first = page.locator("#fpick .file-row").first
    assert first.get_attribute("data-path") == "src/app.py"

    page.keyboard.type("old")
    page.wait_for_function(
        "document.querySelector('#fpick .file-row')?.dataset.path === 'src/old.py'"
    )
    page.keyboard.press("Enter")
    page.wait_for_selector(EDITOR)
    assert page.evaluate("location.hash") == "#files=src/old.py"
    assert page.locator("#fpick").is_hidden()

    page.click(EDITOR)
    page.keyboard.press("Control+End")
    page.keyboard.type("new = 2\n")
    page.wait_for_selector(".fbar .dirty:not([hidden])")
    page.keyboard.press("Control+s")
    page.wait_for_selector(".fbar .dirty[hidden]", state="attached")
    assert (tmp_path / "src" / "old.py").read_text() == "old = 1\nnew = 2\n"
    assert page.errors == []


def test_a_change_on_disk_before_a_save_shows_the_banner_and_reload_takes_it(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    target = tmp_path / "notes.md"
    target.write_text("one\ntwo\n")
    open_file(page, server, "notes.md")
    page.click(EDITOR)
    page.keyboard.press("Control+End")
    page.keyboard.type("mine\n")
    target.write_text("one\nagent\ntwo\n")  # an agent writes under the buffer
    page.click(".fbar .save")
    page.wait_for_selector(".fbanner:not([hidden])")
    assert target.read_text() == "one\nagent\ntwo\n"  # nothing was written

    page.click(".fbanner [data-act=diff]")
    page.wait_for_selector(".fbanner .fdiff .diff")
    page.click(".fbanner [data-act=reload]")
    page.wait_for_selector(".fbanner", state="hidden")
    assert page.evaluate(BUFFER) == "one\nagent\ntwo\n"
    assert page.locator(".fbar .dirty").is_hidden()
    assert page.errors == []


def test_keep_mine_overwrites_the_disk_on_purpose(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    target = tmp_path / "a.txt"
    target.write_text("base\n")
    open_file(page, server, "a.txt")
    page.click(EDITOR)
    page.keyboard.press("Control+End")
    page.keyboard.type("mine\n")
    target.write_text("theirs\n")
    page.keyboard.press("Control+s")
    page.wait_for_selector(".fbanner:not([hidden])")
    page.click(".fbanner [data-act=keep]")
    page.keyboard.press("Control+s")
    page.wait_for_selector(".fbar .dirty[hidden]", state="attached")
    assert target.read_text() == "base\nmine\n"
    assert page.errors == []


def test_leaving_unsaved_edits_asks_first(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    (tmp_path / "a.txt").write_text("base\n")
    open_file(page, server, "a.txt")
    page.click(EDITOR)
    page.keyboard.press("Control+End")
    page.keyboard.type("x")
    page.click("#tab-fleet")
    page.wait_for_selector("#chooser:not([hidden])")
    page.click("#chooser [data-value=cancel]")
    assert page.evaluate("location.hash") == "#files=a.txt"
    page.click("#tab-fleet")
    page.click("#chooser [data-value=save]")
    page.wait_for_selector("#a2[data-view=fleet]")
    assert (tmp_path / "a.txt").read_text() == "base\nx"
    assert page.errors == []


def test_the_view_fits_a_phone(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    (tmp_path / "deep" / "er" / "folder").mkdir(parents=True)
    (
        tmp_path / "deep" / "er" / "folder" / "a_long_file_name_for_a_phone.py"
    ).write_text("x = 1\n" * 40)
    page.set_viewport_size({"width": 390, "height": 800})
    open_file(page, server, "deep/er/folder/a_long_file_name_for_a_phone.py")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert page.locator(".fbar .save").is_visible()
    page.keyboard.press("Alt+o")
    page.wait_for_selector("#fpick:not([hidden]) .file-row")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert page.errors == []
