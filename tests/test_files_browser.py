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
    target = tmp_path / "notes.txt"
    target.write_text("one\ntwo\n")
    open_file(page, server, "notes.txt")
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


def test_a_double_ctrl_s_saves_once_and_the_picker_field_saves_too(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    target = tmp_path / "a.txt"
    target.write_text("base\n")
    open_file(page, server, "a.txt")
    assert page.locator(".fbar .save").is_disabled()  # nothing to save yet
    page.click(EDITOR)
    page.keyboard.press("Control+End")
    page.keyboard.type("one\n")
    page.keyboard.press("Control+s")
    page.keyboard.press("Control+s")
    page.wait_for_selector(".fbar .dirty[hidden]", state="attached")
    assert target.read_text() == "base\none\n"
    assert page.locator(".fbanner").is_hidden()
    log = tmp_path / ".aegis" / "state" / "fs-log.jsonl"
    assert len(log.read_text().splitlines()) == 1

    page.keyboard.type("two\n")
    page.keyboard.press("Alt+o")
    page.wait_for_selector("#fpick:not([hidden])")
    prevented = page.evaluate(
        """() => {
          const ev = new KeyboardEvent('keydown', {key: 's', code: 'KeyS', ctrlKey: true, bubbles: true, cancelable: true});
          document.querySelector('#fpick-q').dispatchEvent(ev);
          return ev.defaultPrevented;
        }"""
    )
    assert prevented  # the browser's Save page does not open
    page.wait_for_function("document.querySelector('.fbar .dirty').hidden")
    assert target.read_text() == "base\none\ntwo\n"
    assert page.errors == []


def test_a_redraw_does_not_move_the_cursor_back_to_the_urls_line(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    (tmp_path / "a.py").write_text("".join(f"x{n} = {n}\n" for n in range(1, 41)))
    open_file(page, server, "a.py:2")
    line = "document.querySelector('.v-files .cm-activeLineGutter').textContent"
    page.wait_for_function(f"{line} === '2'")
    page.keyboard.press("Control+End")
    page.wait_for_function(f"{line} === '41'")
    # What a session added or a reconnect does: render() again, same URL.
    page.evaluate("window.dispatchEvent(new HashChangeEvent('hashchange'))")
    page.keyboard.type("y = 0")
    assert page.evaluate(line) == "41"
    assert page.errors == []


def row(path: str) -> str:
    return f".ftree .trow[data-path='{path}']"


def menu(pg, path: str, item: str) -> None:
    pg.click(row(path), button="right")
    pg.click(f".tmenu button:has-text('{item}')")


def test_the_tree_expands_creates_renames_and_deletes_with_undo(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    write(tmp_path / "src" / "a.py", "a = 1\n", 1_000_000_000)
    (tmp_path / "docs").mkdir()
    page.goto(f"{server.url.split('#')[0]}#files")
    page.wait_for_selector(row("src"))
    assert page.locator(row("src/a.py")).count() == 0
    page.click(row("src"))
    page.wait_for_selector(row("src/a.py"))

    menu(page, "src", "New file")
    page.fill(".ftree .tname", "b.py")
    page.keyboard.press("Enter")
    page.wait_for_selector(EDITOR)
    assert (tmp_path / "src" / "b.py").read_text() == ""
    assert page.evaluate("location.hash") == "#files=src/b.py"
    page.wait_for_selector(row("src/b.py") + ".on")

    menu(page, "src/b.py", "Rename")
    page.fill(".ftree .tname", "c.py")
    page.keyboard.press("Enter")
    page.wait_for_selector(row("src/c.py"))
    assert (tmp_path / "src" / "c.py").exists()
    assert not (tmp_path / "src" / "b.py").exists()
    assert page.evaluate("location.hash") == "#files=src/c.py"  # still open

    page.drag_and_drop(row("src/a.py"), row("docs"))
    page.wait_for_selector(row("src/a.py"), state="detached")
    assert (tmp_path / "docs" / "a.py").read_text() == "a = 1\n"

    menu(page, "src", "Delete")
    page.wait_for_selector("#dialog:not([hidden])")
    assert "1 file" in page.text_content("#dialog .q")
    page.click("#dialog .ok")
    page.wait_for_selector(row("src"), state="detached")
    assert not (tmp_path / "src").exists()
    assert page.evaluate("location.hash") == "#files"  # the open file went too
    page.click(".toast .act")
    page.wait_for_selector(row("src"))
    assert (tmp_path / "src" / "c.py").exists()
    assert page.errors == []


def test_an_agents_write_reloads_an_untouched_buffer(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    target = tmp_path / "notes.txt"
    target.write_text("before\n")
    open_file(page, server, "notes.txt")
    spawn(page, f"/write {target} => after")
    assert target.read_text() == "after"
    page.click("#files-btn")  # back to the view, whose poll finds the change
    page.wait_for_function(f"({BUFFER}) === 'after'", timeout=6000)
    assert page.locator(".fbanner").is_hidden()
    assert page.errors == []


def test_on_a_phone_the_tree_waits_behind_its_button(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    write(tmp_path / "src" / "a.py", "a = 1\n", 1_000_000_000)
    page.set_viewport_size({"width": 390, "height": 800})
    open_file(page, server, "src/a.py")
    assert page.locator(".ftree").is_hidden()
    page.click(".fbar .tbtn")
    page.wait_for_selector(row("src/a.py") + ".on")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.click(row("src/a.py"))
    assert page.locator(".ftree").is_hidden()
    assert page.errors == []


def test_a_file_written_after_its_folder_was_listed_is_revealed(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    write(tmp_path / "src" / "a.py", "a = 1\n", 1_000_000_000)
    open_file(page, server, "src/a.py")
    page.wait_for_selector(row("src/a.py") + ".on")
    (tmp_path / "src" / "new.py").write_text("new = 1\n")  # an agent writes it
    page.evaluate("location.hash = '#files=src/new.py'")
    page.wait_for_selector(row("src/new.py") + ".on")
    assert page.errors == []


def test_renaming_an_open_folder_lists_it_at_its_new_place(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    write(tmp_path / "src" / "a.py", "a = 1\n", 1_000_000_000)
    open_file(page, server, "src/a.py")
    page.wait_for_selector(row("src/a.py"))
    menu(page, "src", "Rename")
    page.fill(".ftree .tname", "lib")
    page.keyboard.press("Enter")
    page.wait_for_selector(row("lib/a.py") + ".on")
    assert page.evaluate("location.hash") == "#files=lib/a.py"
    page.wait_for_timeout(2500)  # a poll after the move finds the file
    assert page.locator(".fbanner").is_hidden()
    assert page.errors == []


def test_a_markdown_file_renders_with_its_relative_image(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    from .test_browser import PNG

    (tmp_path / "docs" / "img").mkdir(parents=True)
    (tmp_path / "docs" / "img" / "dot.png").write_bytes(PNG)
    (tmp_path / "docs" / "guide.md").write_text(
        "# Guide\n\n![dot](img/dot.png)\n\n[up](../src/a.py) and ![out](../../x.png)\n"
    )
    page.goto(f"{server.url.split('#')[0]}#files=docs/guide.md")
    page.wait_for_selector(".v-files .fview .md h1")
    assert page.locator(".v-files .fview .md h1").inner_text() == "Guide"
    page.wait_for_function(
        "document.querySelector('.v-files .fview img[alt=dot]')?.naturalWidth > 0"
    )
    assert (
        page.locator(".v-files .fview img[alt=dot]")
        .get_attribute("src")
        .startswith("/fs/")
    )
    assert page.locator(".v-files .fview img[alt=out]").get_attribute("src") is None
    assert (
        page.locator(".v-files .fview a", has_text="up").get_attribute("href")
        == "#files=src/a.py"
    )
    page.click(".fstrip [data-mode=src]")
    page.wait_for_selector(EDITOR)
    assert page.locator(".v-files .fview").is_hidden()
    assert page.errors == []


def test_an_html_file_runs_in_a_sandbox(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    # The page reports whether it has an origin of its own and the client's
    # storage: a sandboxed frame has neither, so it cannot open the websocket
    # signed in with the client's cookie.
    (tmp_path / "report.html").write_text(
        '<p id="s">no script</p><script>let r;'
        'try { sessionStorage.length; r = "storage open"; }'
        'catch (e) { r = "storage refused"; }'
        'document.getElementById("s").textContent = r + " " + self.origin;</script>'
    )
    page.goto(f"{server.url.split('#')[0]}#files=report.html")
    frame = page.locator(".v-files .fview iframe")
    frame.wait_for()
    assert frame.get_attribute("sandbox") == "allow-scripts"
    report = page.frame_locator(".v-files .fview iframe").locator("#s")
    report.filter(has_text="storage").wait_for()
    assert report.inner_text() == "storage refused null"
    page.click(".fstrip [data-mode=src]")
    page.wait_for_selector(EDITOR)
    assert page.errors == []


def test_a_png_renders_and_an_unknown_file_offers_a_download(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    from .test_browser import PNG

    (tmp_path / "dot.png").write_bytes(PNG)
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
    page.goto(f"{server.url.split('#')[0]}#files=dot.png")
    page.wait_for_function(
        "document.querySelector('.v-files .fstage img')?.naturalWidth > 0"
    )
    page.goto(f"{server.url.split('#')[0]}#files=blob.bin")
    dl = page.locator(".v-files .fother a.btn")
    dl.wait_for()
    href = dl.get_attribute("href")
    assert href.startswith("/fs/") and href.endswith("/blob.bin?download=1")
    origin = server.url.split("/?")[0].split("#")[0]
    assert page.request.get(origin + href).body() == b"\x00\x01\x02" * 100
    assert page.errors == []
