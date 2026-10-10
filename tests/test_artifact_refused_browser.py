"""A submit the server refuses is visible on the artifact's card (#251).

The page used to get the refusal as a JSON-RPC error it only logged, so a click
on Submit with data over the cap did nothing the person could see. The card now
says why, and the page gets a status it can render. Kept out of test_browser.py,
whose server and artifact helpers it borrows."""

import pytest

from aegis import artifacts

from .test_browser import (  # noqa: F401
    CONFIG,
    Server,
    _artifact,
    browser,
    page,
    spawn,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

OVER = artifacts.MAX_STATE_BYTES + 1000


@pytest.fixture
def server(tmp_path, fake_claude, fake_opencode):
    """The browser tests' server, logging at warning. The fixture reads the
    server's stdout only until it prints its URL, and `call artifact.submit`
    logs the whole 64 KB+ params at info: that line fills the pipe and the
    server blocks on its own log until the test ends."""
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode)
    s.args = ["--log-level", "warning"]
    s.start()
    yield s
    s.stop()


# `big` submits data over the cap, `ok` a small answer; `st` shows the last
# status the host pushed, as the page's own script saw it.
BIG_OR_OK = (
    '<button id="big">big</button><button id="ok">ok</button><span id="st"></span>',
    "aegis.onStatus((s) => (st.textContent = JSON.stringify(s)));"
    f'  big.onclick = () => aegis.submit({{blob: "x".repeat({OVER})}}, "Too big");'
    '  ok.onclick = () => aegis.submit({pick: "b"}, "Picked B");',
)


def test_a_refused_submit_shows_its_reason_on_the_card_and_a_valid_one_clears_it(
    server,
    page,  # noqa: F811
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, BIG_OR_OK, 0)
    inner = page.frame_locator(f"iframe[data-artifact={aid}]")
    inner.locator("#big").click()

    note = page.locator(".row.artifact .acard .refused")
    note.wait_for()
    text = note.inner_text()
    assert text.startswith("Not sent: "), text
    assert f"the limit is {artifacts.MAX_STATE_BYTES}" in text, text
    assert "too_large" not in text  # the code is the page's, the card says it in words
    assert note.get_attribute("role") == "alert"
    # Nothing was recorded: the artifact is still live and the page still answers.
    assert page.locator(".row.artifact .acard").get_attribute("data-status") == "live"
    assert page.locator(".row.inbox .from >> text=submit").count() == 0

    # The page's script got the status to render itself.
    assert '"status":"live"' in inner.locator("#st").inner_text()
    assert '"code":"too_large"' in inner.locator("#st").inner_text()
    assert inner.locator("html").get_attribute("data-refused") == text.removeprefix(
        "Not sent: "
    )

    inner.locator("#ok").click()
    page.wait_for_selector(
        ".row.artifact .acard[data-status=submitted] .done >> text=Picked B"
    )
    assert page.locator(".row.artifact .acard .refused").count() == 0
    assert page.errors == []


def test_a_submit_to_an_answered_artifact_says_so_on_its_card(server, page):  # noqa: F811
    # The script no-ops a submit once the page is not live, so this page posts
    # the raw message a page that ignores its status would: the server refuses
    # it with not_live and the card, shown again read-only, says so.
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    again = (
        '<button id="ok">ok</button><button id="again">again</button>',
        '  ok.onclick = () => aegis.submit({pick: "b"}, "Picked B");'
        '  again.onclick = () => parent.postMessage({jsonrpc: "2.0", id: 99, method: "aegis/submit",'
        '    params: {data: {pick: "c"}, label: "Twice"}}, "*");',
    )
    aid, _ = _artifact(page, server, again, 0)
    page.frame_locator(f"iframe[data-artifact={aid}]").locator("#ok").click()
    page.wait_for_selector(
        ".row.artifact .acard[data-status=submitted] .done >> text=Picked B"
    )
    page.locator(".row.artifact button.show").click()
    # The page's stylesheet turns pointer events off once it is not live, so the
    # click is dispatched on the button, as a script on the page could.
    page.frame_locator(".row.artifact iframe").locator("#again").dispatch_event("click")

    note = page.locator(".row.artifact .acard .refused")
    note.wait_for()
    assert note.inner_text() == f"Not sent: {aid} is submitted"
    assert (
        page.locator(".row.artifact .acard").get_attribute("data-status") == "submitted"
    )
    assert page.locator(".row.artifact .done").inner_text().startswith("Picked B")
    assert page.errors == []
