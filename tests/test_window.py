"""``aegis`` with no command: the server, and its URL in an app window (#135).

The real ``aegis`` runs as a process; the browser is a fake that records its
arguments and whether the server was already listening when it was launched.
"""

import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegis import cli, window

# Each process test starts a real server: 1.5 to 3 s on zion, over the budget
# for unmarked tests under load. CI runs slow tests.
slow = pytest.mark.slow


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
        return True
    except OSError:
        return False


def _wait(pred, timeout: float, what: str) -> None:
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)


FAKE_BROWSER = """\
import socket, sys
from urllib.parse import urlsplit
url = sys.argv[-1].removeprefix("--app=")
try:
    socket.create_connection(("127.0.0.1", urlsplit(url).port), timeout=1).close()
    state = "listening"
except OSError:
    state = "refused"
with open({log!r}, "a") as f:
    f.write(" ".join(sys.argv[1:]) + " " + state + "\\n")
"""


@pytest.fixture
def browser(tmp_path: Path) -> tuple[str, Path]:
    log = tmp_path / "browser.log"
    exe = tmp_path / "browser" / "fake-browser"
    exe.parent.mkdir()
    exe.write_text(f"#!{sys.executable}\n" + FAKE_BROWSER.format(log=str(log)))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe), log


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "root"
    r.mkdir()
    (r / ".aegis.yaml").write_text(
        "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    return r


def _aegis(*args: str) -> list[str]:
    return [sys.executable, "-m", "aegis", *args]


@pytest.fixture
def procs():
    started: list[subprocess.Popen] = []
    yield started
    for p in started:
        if p.poll() is None:
            p.terminate()
            p.communicate(timeout=10)


def _serve_window(procs, root: Path, port: int, exe: str) -> subprocess.Popen:
    p = subprocess.Popen(
        _aegis(
            "serve",
            "--window",
            "--root",
            str(root),
            "--port",
            str(port),
            "--browser",
            exe,
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    procs.append(p)
    return p


def _token(root: Path) -> str:
    return (root / ".aegis" / "state" / "token").read_text().strip()


@slow
def test_window_opens_on_the_url_once_the_server_listens(procs, root, browser):
    exe, log = browser
    port = _free_port()
    _serve_window(procs, root, port, exe)
    _wait(log.exists, 15, "the browser to be launched")
    url = f"http://127.0.0.1:{port}/?token={_token(root)}"
    assert log.read_text().splitlines() == [f"--app={url} listening"]


@slow
def test_a_running_server_for_the_root_just_gets_a_window(procs, root, browser):
    exe, log = browser
    port = _free_port()
    first = _serve_window(procs, root, port, exe)
    _wait(log.exists, 15, "the first window")
    again = subprocess.run(
        _aegis(
            "serve",
            "--window",
            "--root",
            str(root),
            "--port",
            str(port),
            "--browser",
            exe,
        ),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert again.returncode == 0, again.stderr
    assert "already serving" in again.stdout
    url = f"http://127.0.0.1:{port}/?token={_token(root)}"
    assert log.read_text().splitlines() == [f"--app={url} listening"] * 2
    assert first.poll() is None, "the running server must be left alone"

    plain = subprocess.run(
        _aegis("serve", "--root", str(root), "--port", str(port), "--browser", exe),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert plain.returncode == 1, "without --window a taken port is still an error"
    assert len(log.read_text().splitlines()) == 2


@slow
@pytest.mark.parametrize("ran_before", [False, True])
def test_a_port_held_by_something_else_opens_nothing(root, browser, ran_before):
    exe, log = browser
    if ran_before:
        # A token from an earlier run, so aegis has to ask the squatter and get
        # no welcome, rather than giving up on a missing token.
        state = root / ".aegis" / "state"
        state.mkdir(parents=True)
        (state / "token").write_text("an-old-token\n")
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        port = squatter.getsockname()[1]
        r = subprocess.run(
            _aegis(
                "serve",
                "--window",
                "--root",
                str(root),
                "--port",
                str(port),
                "--browser",
                exe,
            ),
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert r.returncode == 1
    assert f"port {port}" in r.stderr
    assert not log.exists()


@slow
def test_another_roots_server_is_not_ours(procs, root, browser, tmp_path):
    exe, log = browser
    port = _free_port()
    _serve_window(procs, root, port, exe)
    _wait(log.exists, 15, "the first window")
    other = tmp_path / "other"
    other.mkdir()
    (other / ".aegis.yaml").write_text((root / ".aegis.yaml").read_text())
    # It has run before, so it has a token to try, and the server refuses it.
    (other / ".aegis" / "state").mkdir(parents=True)
    (other / ".aegis" / "state" / "token").write_text("other-roots-token\n")
    r = subprocess.run(
        _aegis(
            "serve",
            "--window",
            "--root",
            str(other),
            "--port",
            str(port),
            "--browser",
            exe,
        ),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert r.returncode == 1
    assert len(log.read_text().splitlines()) == 1


@slow
def test_serve_without_window_opens_nothing(procs, root, browser, monkeypatch):
    exe, log = browser
    port = _free_port()
    monkeypatch.setenv("AEGIS_BROWSER", exe)
    p = subprocess.Popen(
        _aegis("serve", "--root", str(root), "--port", str(port)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    procs.append(p)
    _wait(lambda: _listening(port), 15, "the server")
    time.sleep(1)
    assert not log.exists()


def test_the_window_waits_for_the_port(monkeypatch):
    # The fake browser above starts too slowly to catch an early launch; this
    # holds the port closed until the window has had time to open by mistake.
    port = _free_port()
    opened: list[bool] = []
    monkeypatch.setattr(
        window, "open_window", lambda url, b: opened.append(_listening(port))
    )
    cli._open_when_listening("127.0.0.1", port, "http://unused", None)
    time.sleep(0.3)
    assert opened == []
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))
        s.listen()
        _wait(lambda: opened, 3, "the window")
    assert opened == [True]


def test_bare_aegis_is_serve_with_a_window(monkeypatch):
    import inspect

    params = set(inspect.signature(cli.serve).parameters)
    seen = {}
    monkeypatch.setattr(cli, "serve", lambda **kw: seen.update(kw))
    r = CliRunner().invoke(cli.app, [])
    assert r.exit_code == 0, r.output
    assert seen["window"] is True
    assert seen["port"] == cli.DEFAULT_PORT
    # Called directly, serve gets typer's OptionInfo for anything not passed.
    assert set(seen) == params


def test_the_browser_is_the_one_asked_for_then_the_first_on_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "path"
    bin_dir.mkdir()
    for name in ("chromium", "google-chrome-beta"):
        exe = bin_dir / name
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(window, "MAC_APPS", ())
    monkeypatch.setattr(window, "desktop_default", lambda: None)
    assert window.find_browser("/opt/my-chrome") == "/opt/my-chrome"
    assert window.find_browser(None) == str(bin_dir / "google-chrome-beta")
    (bin_dir / "google-chrome-beta").unlink()
    assert window.find_browser(None) == str(bin_dir / "chromium")
    (bin_dir / "chromium").unlink()
    assert window.find_browser(None) is None


def test_the_desktop_default_wins_when_it_is_chromium(tmp_path, monkeypatch):
    bin_dir = tmp_path / "path"
    bin_dir.mkdir()
    for name in ("google-chrome", "google-chrome-beta", "firefox"):
        exe = bin_dir / name
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(window, "desktop_default", lambda: "google-chrome-beta")
    assert window.find_browser(None) == str(bin_dir / "google-chrome-beta")
    # Firefox has no app mode; the first Chromium-family browser stands in.
    monkeypatch.setattr(window, "desktop_default", lambda: "firefox")
    assert window.find_browser(None) == str(bin_dir / "google-chrome")


def test_the_desktop_default_is_read_from_xdg_settings(tmp_path, monkeypatch):
    bin_dir = tmp_path / "path"
    bin_dir.mkdir()
    exe = bin_dir / "xdg-settings"
    exe.write_text(
        '#!/bin/sh\n[ "$*" = "get default-web-browser" ] && echo google-chrome-beta.desktop\n'
    )
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert window.desktop_default() == "google-chrome-beta"
    exe.unlink()
    assert window.desktop_default() is None
