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
    link = page.locator(".row.prose code.ref").first
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
    page.locator(".row.prose code.ref").first.click()
    page.locator("#viewer .code .mark").wait_for()
    assert page.locator("#viewer .code .mark").get_attribute("data-line") == "2"
    assert page.errors == []


def test_a_missing_file_says_where_it_looked(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, "/md See `gone/none.py`.")
    page.locator(".row.prose code.ref[data-kind=missing]").wait_for()
    page.locator(".row.prose code.ref").first.click()
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
    page.locator(".row.prose code.ref").first.click()
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
    page.fill("#sp-text", "/md See `src/far.py:1` and `octo/aegis#1`.")
    page.click("#sp-go")
    page.wait_for_selector("#a2[data-view=session]")
    turns_done(page, 1)
    code = page.locator(".row.prose .md code")
    assert code.first.inner_text() == "src/far.py:1", "the span is drawn"
    assert page.locator(".row.prose code.ref").count() == 0, (
        "the file is on the other machine"
    )
    assert (
        page.locator(".row.prose a.repo").get_attribute("href")
        == "https://github.com/octo/aegis/issues/1"
    ), "an issue ref needs no disk"
    assert page.errors == []


def test_repos_and_urls_link_without_the_disk(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(
        page,
        "/md Fixed in `octo/aegis#358`, see https://github.com/octo/aegis/pull/358, "
        "https://pypi.org/project/aegis-agents and [the guide](https://docs.example.org/guide/install). "
        "Also [`src/a.py`](https://docs.example.org/a).",
    )
    repo = page.locator(".row.prose a.repo")
    repo.first.wait_for()
    assert (
        repo.first.get_attribute("href") == "https://github.com/octo/aegis/issues/358"
    )
    assert repo.first.locator("use").get_attribute("href") == "#g-github"
    auto = page.locator(".row.prose a.web.auto")
    assert auto.all_inner_texts() == ["octo/aegis#358", "aegis-agents"]
    assert auto.first.get_attribute("title") == "https://github.com/octo/aegis/pull/358"
    assert auto.nth(1).locator("use").get_attribute("href") == "#g-pypi"
    guide = page.locator(".row.prose a.web:not(.auto)").first
    assert guide.inner_text() == "the guide", "a link the agent named keeps its text"
    assert guide.locator("use").get_attribute("href") == "#g-globe"
    assert page.locator(".row.prose a code.ref, .row.prose a a").count() == 0, (
        "a span inside a link stays the agent's link"
    )
    assert page.errors == []


def test_each_path_shows_what_it_names_once_in_view(server, page):  # noqa: F811
    (server.root / "src").mkdir()
    (server.root / "src" / "a.py").write_text("a = 1\n")
    page.goto(server.url)
    spawn(page, "/md See `src/a.py:1`, `src/`, `gone/x.py`, `octo/aegis` and `a/b/c`.")
    page.locator(".row.prose code.ref[data-kind=missing]").wait_for()
    kinds = page.eval_on_selector_all(
        ".row.prose code.ref", "els => els.map(e => [e.textContent, e.dataset.kind])"
    )
    assert kinds == [["src/a.py:1", "file"], ["src/", "dir"], ["gone/x.py", "missing"]]
    icons = page.eval_on_selector_all(
        ".row.prose code.ref use", "els => els.map(e => e.getAttribute('href'))"
    )
    assert icons == ["#g-file", "#g-folder", "#g-file-gone"]
    assert (
        page.locator(".row.prose a.repo").get_attribute("href")
        == "https://github.com/octo/aegis"
    )
    plain = page.locator(".row.prose .md code", has_text="a/b/c")
    assert plain.get_attribute("class") in (None, ""), (
        "a missing folder shape is plain code"
    )
    page.locator(".row.prose code.ref[data-kind=dir]").click()
    assert page.locator("#viewer").is_hidden(), "a folder opens nothing"
    assert page.errors == []


def test_a_late_answer_from_another_session_is_dropped(server, page):  # noqa: F811
    # Entry ids repeat across sessions: an answer for session A's e1.0 that
    # lands after the switch must not draw on session B's e1.0.
    page.goto(server.url)
    got = page.evaluate(
        """async () => {
          const { Transcript } = await import('/static/js/transcript.js');
          const sc = document.createElement('div');
          sc.style.cssText = 'position:fixed;top:0;left:0;width:600px;height:400px;overflow:auto;z-index:9999';
          const list = document.createElement('div');
          const jump = document.createElement('button');
          sc.append(list);
          document.body.append(sc, jump);
          let release;
          const calls = [];
          const t = new Transcript(sc, list, jump, {
            loadDetail: async () => [],
            statPaths: (id) => {
              calls.push(id);
              if (calls.length === 1) return new Promise((r) => { release = r; });
              return Promise.resolve({ kinds: ['file'] });
            },
          });
          const row = (md, path) => ({ id: 'e1.0', rev: 1, kind: 'prose', status: 'ok', ts: null,
            glyph: 'prose', title: '', summary: '', md, detail: { paths: [path] } });
          const tick = () => new Promise((r) => setTimeout(r, 250));
          t.snapshot({ rev: 1, entries: [row('See `a/b.py`.', 'a/b.py')] });
          await tick();
          t.snapshot({ rev: 1, entries: [row('See `c/d.py`.', 'c/d.py')] });
          await tick();
          release({ kinds: ['missing'] });
          await tick();
          const code = list.querySelector('code.ref');
          return { calls: calls.length, kind: code && code.dataset.kind };
        }"""
    )
    assert got == {"calls": 2, "kind": "file"}
    assert page.errors == []
