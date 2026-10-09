"""The label js/artifact.js sends with a submit is one the server accepts.

The page cuts and repairs the label itself, because a refused submit only logs
in the frame and the person sees nothing happen. The rule lives twice, in
`artifacts.LABEL_MAX` with `PageSubmit.label` and in artifact.js, so this runs
the real script in Chromium and validates every label it posts with the
server's own model: if either side moves, a case here fails.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis import artifacts
from aegis.artifact_ops import PageSubmit

pytestmark = [pytest.mark.browser, pytest.mark.slow]

playwright = pytest.importorskip("playwright.sync_api")

SCRIPT = (
    Path(__file__).parents[1] / "src" / "aegis" / "client" / "js" / "artifact.js"
).read_text()

MAX = artifacts.LABEL_MAX
EMOJI = "\U0001f600"  # two UTF-16 units, one code point

# A page whose parent records what the frame posts: the frame sees a host, so
# aegis.submit really posts instead of only logging.
HOST = """
<script>window.got = [];
addEventListener("message", (e) => { if (e.data.method === "aegis/submit") got.push(e.data.params); });
</script>
<iframe id="f"></iframe>
"""

CASES = {
    "plain": "Picked B",
    "empty": "",
    "null": None,
    "starts with a newline": "\nPicked B",
    "only newlines": "\n\n",
    "second line dropped": "first\nsecond",
    "crlf": "first\r\nsecond",
    "exactly the max": "x" * MAX,
    "one over the max": "x" * (MAX + 1),
    "long": "x" * 5000,
    "emoji across the cut": "x" * (MAX - 1) + EMOJI + "tail",
    "emoji ending at the max": "x" * (MAX - 1) + EMOJI,
    "emoji only, past the max": EMOJI * (MAX + 10),
    "lone high surrogate": "a\ud83db",
    "lone low surrogate": "\ude00",
    "number": 7,
}


def _sent(browser) -> list:
    """What the frame posts for each case, in CASES order, as the host's JSON."""
    pg = browser.new_page()
    try:
        pg.set_content(HOST)
        pg.evaluate(
            """([script, labels]) => {
                const f = document.getElementById("f");
                f.srcdoc = "<script>" + script + "<\\/script>";
                return new Promise((resolve) => f.addEventListener("load", () => {
                    for (const l of labels) f.contentWindow.aegis.submit({}, l);
                    resolve();
                }));
            }""",
            [SCRIPT, list(CASES.values())],
        )
        pg.wait_for_function(f"window.got.length >= {len(CASES)}")
        return pg.evaluate("window.got")
    finally:
        pg.close()


@pytest.fixture(scope="module")
def sent():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield _sent(b)
        finally:
            b.close()


def test_every_label_the_page_sends_is_one_the_server_accepts(sent):
    refused = {}
    for name, params in zip(CASES, sent):
        try:
            PageSubmit.model_validate_json(
                json.dumps({"log_id": "l", "artifact_id": "a"} | params)
            )
        except ValidationError as e:
            refused[name] = (params["label"][:20], e.errors()[0]["type"])
    assert refused == {}


def test_a_label_is_cut_to_a_line_of_code_points_not_dropped(sent):
    got = dict(zip(CASES, (p["label"] for p in sent)))
    assert got["plain"] == "Picked B"
    assert got["empty"] == got["null"] == got["only newlines"] == "answered"
    assert got["starts with a newline"] == "answered"
    assert got["second line dropped"] == "first"
    assert got["exactly the max"] == "x" * MAX
    assert got["long"] == "x" * MAX
    assert got["emoji across the cut"] == "x" * (MAX - 1) + EMOJI
    assert got["emoji only, past the max"] == EMOJI * MAX
    assert got["number"] == "7"
