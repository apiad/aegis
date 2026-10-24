# ruff: noqa: F811  (fixtures imported from test_browser)
"""The knowledge switch in Settings: on, off again, at desktop and phone
width, written to the person's own setup record."""

import getpass
import json

import pytest

from .test_browser import browser, open_settings, page, settings_server  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def record(server) -> dict:
    path = server.root / ".aegis" / "state" / "setup" / f"{getpass.getuser()}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def test_the_switch_turns_native_on_and_off(settings_server, page):
    open_settings(page, settings_server.url)
    box = page.locator("#set-native")
    page.wait_for_function("!document.querySelector('#set-native').disabled")
    assert not box.is_checked()
    assert "Off:" in page.inner_text("#set-native-note")
    box.check()
    page.wait_for_function(
        "document.querySelector('#set-native-note').textContent.startsWith('Saved.')"
    )
    assert record(settings_server)["knowledge"] == "native"
    assert "On:" in page.inner_text("#set-native-note")
    # A redraw of the form keeps it, and a reload reads it back.
    page.reload()
    open_settings(page, settings_server.url)
    page.wait_for_function("document.querySelector('#set-native').checked")
    page.locator("#set-native").uncheck()
    page.wait_for_function(
        "document.querySelector('#set-native-note').textContent.includes('Off:')"
    )
    assert record(settings_server)["knowledge"] == "side-by-side"
    assert page.errors == []


def test_the_switch_fits_a_phone(settings_server, page):
    page.set_viewport_size({"width": 390, "height": 844})
    open_settings(page, settings_server.url)
    page.wait_for_selector("#set-knowledge")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    b = page.locator("#set-knowledge").bounding_box()
    assert b["x"] >= 0 and b["x"] + b["width"] <= 390
    assert page.errors == []


# The first knowledge.setting the page sends is renamed to an op the server
# does not have, so that read fails as an offline one would.
FAIL_FIRST = """() => {
  const send = WebSocket.prototype.send;
  let n = 0;
  WebSocket.prototype.send = function (data) {
    if (typeof data === "string" && data.includes('"op":"knowledge.setting"') && n++ === 0)
      data = data.replace('"op":"knowledge.setting"', '"op":"knowledge.nosuch"');
    send.call(this, data);
  };
}"""


def test_a_failed_first_read_leaves_the_switch_usable_with_a_retry(
    settings_server, page, browser
):
    page.add_init_script(f"({FAIL_FIRST})()")
    open_settings(page, settings_server.url)
    page.wait_for_selector("#set-native-retry")
    assert not page.locator("#set-native").is_disabled()
    assert "could not be read" in page.inner_text("#set-native-note")
    page.click("#set-native-retry")
    page.wait_for_function(
        "document.querySelector('#set-native-note').textContent.startsWith('Off:')"
    )
    assert page.locator("#set-native-retry").is_hidden()
    # Each open of Settings reads it again: a change made in another tab shows.
    other = browser.new_page()
    open_settings(other, settings_server.url)
    other.wait_for_function("!document.querySelector('#set-native').disabled")
    other.locator("#set-native").check()
    other.wait_for_function(
        "document.querySelector('#set-native-note').textContent.startsWith('Saved.')"
    )
    other.close()
    page.keyboard.press("Alt+Digit0")  # the Fleet, then Settings again
    page.wait_for_selector("#a2[data-view=fleet]")
    page.keyboard.press("Alt+KeyS")
    page.wait_for_function("document.querySelector('#set-native').checked")
    assert record(settings_server)["knowledge"] == "native"
