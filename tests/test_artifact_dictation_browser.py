"""aegis.dictate and aegis.upload, through a real frame and a real server (#858).

A page's origin is opaque, so the microphone refuses it and a file does not fit
through the 64 KB bridge: the host runs both for the page. The engine is the
fixture's stub, which answers ``[2.5s kw=2]``, and the microphone is Chromium's
fake device. Kept out of test_browser.py, whose server and helpers it borrows."""

import re
from pathlib import Path

import pytest

from aegis.dictation import PINS, pin_id

from .test_browser import (  # noqa: F401
    Linked,
    _artifact,
    beta_session,
    browser,
    dict_server,
    mic_page,
    page,
    server,
    spawn,
    turns_done,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

# `mic` toggles dictation into `q`, `go` submits what `q` holds, and `st` shows
# the states the page heard. `auto` tries to dictate with no click at all.
FIELD = (
    '<textarea id="q"></textarea><button id="mic">mic</button>'
    '<button id="go">go</button><span id="st"></span><span id="said"></span>'
    '<span id="ref"></span>',
    "const hear = (s) => (st.textContent = s);"
    "  mic.onclick = () => aegis.dictate(q, hear);"
    "  q.addEventListener('input', () => (said.textContent = q.value));"
    "  aegis.onStatus((s) => (ref.textContent = s.refused?.code || ''));"
    '  go.onclick = () => aegis.submit({q: q.value}, "Answered");',
)
AUTO = (
    '<textarea id="q"></textarea><span id="st"></span>',
    "setTimeout(() => aegis.dictate(q, (s) => (st.textContent = s)), 6500);"
    "  aegis.state({});",
)
FILE = (
    '<input id="f" type="file"><span id="got"></span><span id="pc"></span>',
    "f.onchange = async () => {"
    "    const r = await aegis.upload(f.files[0], (sent, size) => (pc.textContent = `${sent}/${size}`));"
    "    got.textContent = JSON.stringify(r);"
    "  };",
)


def _open(pg, srv, body):
    pg.goto(srv.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg)
    aid, _ = _artifact(pg, srv, body, 0)
    return aid, pg.frame_locator(f"iframe[data-artifact={aid}]")


def _listening(pg, inner, seconds=2.5):
    inner.locator("#mic").click()
    inner.locator("#st", has_text="listening").wait_for(timeout=10000)
    pg.wait_for_timeout(int(seconds * 1000))


def test_a_click_in_the_page_dictates_into_its_field_and_the_agent_is_told(
    dict_server,  # noqa: F811
    mic_page,  # noqa: F811
):
    pg = mic_page
    aid, inner = _open(pg, dict_server, FIELD)
    q = inner.locator("#q")
    q.fill("Look at")
    bar = pg.locator(f".stage:has(iframe[data-artifact={aid}]) > .dictbar")
    _listening(pg, inner)
    assert "Listening" in bar.inner_text()
    assert inner.locator("#q").get_attribute("data-dictation") == "listening"
    # A word typed while it listens survives what the host writes next.
    q.press("Control+Home")
    q.press_sequentially("Now. ")
    pg.wait_for_timeout(100)
    inner.locator("#mic").click()
    inner.locator("#st", has_text="idle").wait_for(timeout=10000)
    bar.wait_for(state="detached")
    v = q.input_value()
    assert re.fullmatch(r"Now\. Look at \[\d\.\ds kw=\d+\]", v), v
    assert inner.locator("#said").inner_text() == v, "the page hears the text as input"
    assert pg.get_attribute("#mic", "data-state") == "idle", "the composer's is its own"

    inner.locator("#go").click()
    row = pg.locator(f".row.inbox:has(.from:has-text('artifact:{aid} · submit'))")
    row.wait_for(timeout=10000)
    assert "dictated by voice" in row.text_content()
    assert pg.errors == []


def test_a_page_cannot_start_the_microphone_on_its_own(
    dict_server,  # noqa: F811
    mic_page,  # noqa: F811
):
    pg = mic_page
    _, inner = _open(pg, dict_server, AUTO)
    # Playwright's evaluate counts as a gesture and a frame's locator polls with
    # it, so nothing may touch the page until the page's call, 6.5 s after load,
    # has been answered: by then the last real activation has run out.
    pg.wait_for_timeout(8000)
    assert inner.locator("#st").inner_text() == "idle"
    assert pg.locator(".dictbar").count() == 0
    assert "click in the page" in pg.locator(".toast").last.inner_text()


def test_a_submit_while_listening_is_refused_and_keeps_the_words(
    dict_server,  # noqa: F811
    mic_page,  # noqa: F811
):
    pg = mic_page
    aid, inner = _open(pg, dict_server, FIELD)
    _listening(pg, inner)
    inner.locator("#go").click()
    inner.locator("#ref", has_text="dictating").wait_for(timeout=5000)
    note = pg.locator(".row.artifact .acard .refused")
    assert "dictation was still going" in note.inner_text()
    assert pg.locator(".row.artifact .acard").get_attribute("data-status") == "live"
    inner.locator("#st", has_text="idle").wait_for(timeout=10000)
    assert "kw=" in inner.locator("#q").input_value()
    inner.locator("#go").click()
    pg.locator(".row.artifact .acard[data-status=submitted]").wait_for(timeout=10000)
    row = pg.locator(f".row.inbox:has(.from:has-text('artifact:{aid} · submit'))")
    row.wait_for(timeout=10000)
    assert "kw=" in row.text_content()
    assert pg.errors == []


def test_leaving_the_page_stops_the_microphone(
    dict_server,  # noqa: F811
    mic_page,  # noqa: F811
):
    pg = mic_page
    pg.add_init_script(
        "const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);"
        "window.__mics = [];"
        "navigator.mediaDevices.getUserMedia = async (c) => {"
        "  const s = await gum(c); __mics.push(s); return s; };"
    )
    _, inner = _open(pg, dict_server, FIELD)
    _listening(pg, inner, 0.5)
    live = "() => __mics.flatMap((s) => s.getTracks()).filter((t) => t.readyState === 'live').length"
    assert pg.evaluate(live) == 1, "the check below must have something to see stop"
    spawn(pg)
    pg.wait_for_function(f"() => ({live})() === 0", timeout=10000)
    assert pg.errors == []


def test_a_file_picked_in_the_page_reaches_the_inbox_and_wakes_the_agent(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path: Path,
):
    # Over one 256 KB chunk and over the bridge's 64 KB.
    body = bytes(range(256)) * 2000
    src = tmp_path / "voice note.m4a"
    src.write_bytes(body)
    aid, inner = _open(page, server, FILE)
    inner.locator("#f").set_input_files(str(src))
    got = inner.locator("#got")
    got.locator("text=path").wait_for(timeout=15000)
    assert inner.locator("#pc").inner_text() == f"{len(body)}/{len(body)}"
    path = re.search(r'"path":"([^"]+)"', got.inner_text()).group(1)
    assert Path(path).read_bytes() == body
    row = page.locator(f".row.inbox:has(.from:has-text('artifact:{aid} · upload'))")
    row.wait_for(timeout=10000)
    assert path in row.text_content()
    assert page.locator(".row.artifact .acard").get_attribute("data-status") == "live"
    assert page.errors == []


@pytest.fixture
def far(tmp_path: Path, fake_claude: str, fake_opencode: str):
    """Two servers, the browser on alpha, the session and its page on beta, with
    the engine on alpha, which is the one serving the browser's page."""
    d = tmp_path / "dictation" / pin_id()
    d.mkdir(parents=True)
    stub = (Path(__file__).parent / "fixtures" / "dictation" / "needle.js").read_bytes()
    for p in PINS:
        (d / p.name).write_bytes(stub if p.name == "needle.js" else b"stub")
    pair = Linked(tmp_path, fake_claude, fake_opencode)
    pair.alpha.env["AEGIS_DICTATION_DIR"] = str(d.parent)
    yield pair.start()
    pair.stop()


def test_a_linked_servers_page_dictates_and_uploads_and_the_agent_is_told(
    far,
    mic_page,  # noqa: F811
    tmp_path: Path,
):
    pg = mic_page
    lid = beta_session(far, pg.context.browser)
    pg.goto(f"{far.alpha.url}#s=beta/{lid}")
    pg.wait_for_selector("#a2[data-view=session]")
    turns_done(pg, 1)
    aid, _ = _artifact(pg, far.beta, (FIELD[0] + FILE[0], FIELD[1] + FILE[1]), 1)
    inner = pg.frame_locator(f"iframe[data-artifact={aid}]")

    src = tmp_path / "note.m4a"
    src.write_bytes(b"x" * 300_000)
    inner.locator("#f").set_input_files(str(src))
    inner.locator("#got").locator("text=path").wait_for(timeout=15000)
    path = re.search(r'"path":"([^"]+)"', inner.locator("#got").inner_text()).group(1)
    assert Path(path).is_relative_to(far.beta.root), "the far server's inbox"
    assert Path(path).read_bytes() == src.read_bytes()
    row = pg.locator(f".row.inbox:has(.from:has-text('artifact:{aid} · upload'))")
    row.wait_for(timeout=10000)

    _listening(pg, inner)
    inner.locator("#mic").click()
    inner.locator("#st", has_text="idle").wait_for(timeout=10000)
    assert "kw=" in inner.locator("#q").input_value()
    inner.locator("#go").click()
    row = pg.locator(f".row.inbox:has(.from:has-text('artifact:{aid} · submit'))")
    row.wait_for(timeout=10000)
    assert "kw=" in row.text_content()
    assert "dictated by voice" in row.text_content()
    assert pg.errors == []
