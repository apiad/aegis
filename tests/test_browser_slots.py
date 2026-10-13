"""The host-wide browser cap in conftest holds: no more than N browsers at
once across every run on the host (#360). Threads stand in for runs, because
flock locks an open file, not a process."""

import threading
import time

import pytest

from . import conftest


@pytest.fixture
def two_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "BROWSER_SLOT_DIR", tmp_path / "slots")
    monkeypatch.setattr(conftest, "_slots", 2)


def test_no_more_browsers_run_at_once_than_there_are_slots(two_slots):
    inside, most, lock = 0, 0, threading.Lock()

    def run():
        nonlocal inside, most
        with conftest.browser_slot():
            with lock:
                inside += 1
                most = max(most, inside)
            time.sleep(0.05)
            with lock:
                inside -= 1

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most == 2


def test_a_run_that_finds_no_free_slot_fails_by_name(two_slots, monkeypatch):
    monkeypatch.setattr(conftest, "BROWSER_SLOT_WAIT_S", 0.2)
    with conftest.browser_slot(), conftest.browser_slot():
        with pytest.raises(pytest.fail.Exception, match="no browser slot free"):
            with conftest.browser_slot():
                pass


def test_the_env_var_sets_the_number_of_slots(monkeypatch):
    monkeypatch.setattr(conftest, "_slots", None)
    monkeypatch.setenv("AEGIS_TEST_BROWSERS", "3")
    assert conftest.browser_slots() == 3


def test_a_browser_launched_outside_a_slot_is_refused():
    sync_api = pytest.importorskip("playwright.sync_api")
    with pytest.raises(AssertionError, match="browser_slot"):
        sync_api.BrowserType.launch(None)
