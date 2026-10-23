"""The new tab's rows under the chips (#580): the rotating tip from the help
pages, /feedback, and the doctor row with /doctor, against a real `aegis
serve` and the fake claude. Fixtures from test_browser.py."""

from pathlib import Path

import pytest

from .test_browser import CONFIG, Server, browser, page  # noqa: F401
from .test_tutorial_browser import eventually, meta
from .test_whats_new_browser import open_new_tab

pytestmark = [pytest.mark.browser, pytest.mark.slow]

BROKEN = "queues:\n  cheap: {agent: haiku, max_parallel: 1}\n"


@pytest.fixture
def served(tmp_path: Path, fake_claude: str, fake_opencode: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def test_the_tip_rotates_holds_under_the_pointer_and_opens_its_page(
    served,
    page,  # noqa: F811
):
    page.clock.install()
    page.goto(served.url)
    open_new_tab(page)
    assert page.is_visible("#hint-tip") and page.is_visible("#hint-feedback")
    first = page.inner_text("#hint-tip-go")
    page.clock.run_for(8400)
    second = page.inner_text("#hint-tip-go")
    assert second and second != first
    page.hover("#hint-tip-go")
    page.clock.run_for(20000)
    assert page.inner_text("#hint-tip-go") == second, "held under the pointer"
    ref = page.get_attribute("#hint-tip-go", "data-ref")
    page.click("#hint-tip-go")
    page.wait_for_selector("#a2[data-view=help]")
    assert page.evaluate("decodeURIComponent(location.hash)") == f"#help={ref}"
    assert page.errors == []


def test_feedback_starts_a_session_with_the_chips(served, page):  # noqa: F811
    page.goto(served.url)
    open_new_tab(page)
    page.click("#hint-feedback-go")
    page.wait_for_selector("#a2[data-view=session]")
    log_id = page.evaluate("location.hash.slice(3)")
    assert meta(served, log_id)["title"] == "Feedback"
    open_new_tab(page)
    assert page.is_visible("#hint-feedback"), "it never hides"
    assert page.errors == []


def test_the_doctor_row_shows_a_broken_setup_and_doctor_fixes_it(
    served,
    page,  # noqa: F811
):
    page.goto(served.url)
    open_new_tab(page)
    assert page.is_hidden("#hint-doctor")
    (served.root / ".aegis.yaml").write_text(CONFIG + BROKEN)

    def shown():
        page.goto(served.url + "#fleet")
        open_new_tab(page)
        return page.is_visible("#hint-doctor")

    eventually(shown, timeout=30)
    assert "1 error" in page.inner_text("#hint-doctor-sum")
    assert page.is_hidden("#hint-doctor-list")
    page.click("#hint-doctor-more")
    items = page.inner_text("#hint-doctor-list")
    assert "Queue cheap" in items and "haiku" in items and "Settings" in items
    page.click("#hint-doctor-go")
    page.wait_for_selector("#a2[data-view=session]")
    log_id = page.evaluate("location.hash.slice(3)")
    m = meta(served, log_id)
    assert m["title"].startswith("Doctor: 1 error")
    (served.root / ".aegis.yaml").write_text(CONFIG)

    # The host's own systemd unit may add a warning of its own (service.check),
    # so the broken queue's finding going is the check, not the whole row.
    def gone():
        page.goto(served.url + "#fleet")
        open_new_tab(page)
        return page.is_hidden("#hint-doctor") or "haiku" not in page.inner_html(
            "#hint-doctor-list"
        )

    eventually(gone, timeout=30)
    assert page.errors == []
