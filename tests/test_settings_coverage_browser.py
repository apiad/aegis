"""Settings draws a control for every key .aegis.yaml takes, and saves the
keys it gained (#527). The keys are the readers' own (test_config_coverage)."""

# ruff: noqa: F811  (pytest fixtures imported from test_browser)

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from .test_browser import Server, browser, open_settings, page, pick  # noqa: F401
from .test_config_coverage import ALIASES, FULL, reader_keys

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def full_server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    (tmp_path / ".aegis.yaml").write_text(FULL)
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def test_every_key_of_the_file_has_a_control(full_server, page):
    open_settings(page, full_server.url)
    page.wait_for_selector("#set-network")
    keys = sorted({ALIASES.get(k, k) for k in reader_keys()})
    missing = page.evaluate(
        'keys => keys.filter(k => !document.querySelector(`[data-key~="${k}"]`))',
        keys,
    )
    assert missing == []
    assert page.errors == []


def test_the_new_controls_save_to_the_file(full_server, page):
    open_settings(page, full_server.url)
    page.wait_for_selector("#set-network")
    page.fill('[data-key="context.handoff_at"]', "")  # back to the default
    page.fill('[data-key="nudges.silent_s"]', "5")
    pick(page, '[data-key="notices.disk"]', "off")
    page.wait_for_selector('[data-key="notices.disk.on"]', state="detached")
    pick(page, '[data-key="queues.*.quiet"]', "quiet")
    pick(page, '[data-key="btw.agent"]', "off")
    page.fill('[data-key="terminals.*.cmd"]', "make serve")
    keep = '[data-row="agents.opus"] [data-key="agents.*.keep_harness_tools"]'
    page.click(f'{keep} button:text-is("Monitor")')  # open: it keeps Agent
    page.click("#set-save")
    page.wait_for_function(
        "document.querySelector('#set-status').textContent === 'Saved'"
    )
    data = YAML(typ="safe").load((full_server.root / ".aegis.yaml").read_text())
    assert "handoff_at" not in data["context"]
    assert data["nudges"]["silent_s"] == 5
    assert data["notices"]["disk"] is False
    assert "quiet" not in data["queues"]["general"]
    assert "btw" not in data
    assert data["terminals"]["api"]["cmd"] == "make serve"
    assert data["agents"]["opus"]["keep_harness_tools"] == ["Agent", "Monitor"]
    assert page.errors == []


def test_a_wrong_value_is_refused_on_its_block(full_server, page):
    open_settings(page, full_server.url)
    page.wait_for_selector("#set-network")
    page.fill('[data-key="network.interval"]', "0")
    page.click("#set-save")
    page.wait_for_selector('[data-row="network"] .set-why.error')
    assert "network.interval" in page.text_content('[data-row="network"] .set-why')
    assert page.errors == []
