import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegis import service
from aegis.cli import app
from aegis.service import GIB, Unit, caps, check, render

README = Path(__file__).parent.parent / "README.md"
# The flags that give the README's example unit, on a host with 8 GB.
EXAMPLE = [
    "--user", "aegis", "--root", "/srv/aegis", "--port", "8742",
    "--aegis", "/usr/local/bin/aegis",
]  # fmt: skip


def meminfo(tmp_path, gib: float) -> Path:
    p = tmp_path / "meminfo"
    p.write_text(f"MemTotal:       {int(gib * 1024 * 1024)} kB\nMemFree: 1 kB\n")
    return p


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A host with 8 GB, PATH set to the README's, and no unit installed."""
    monkeypatch.setattr(service, "MEMINFO", meminfo(tmp_path, 8))
    monkeypatch.setattr(service, "UNIT_DIRS", ((tmp_path / "system", False),))
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def invoke(*args: str, input: str | None = None):
    return CliRunner().invoke(app, list(args), input=input, env={"COLUMNS": "200"})


def test_caps_follow_the_host_and_stay_apart():
    assert caps(8 * GIB) == ("5.5G", "6.5G")
    assert caps(16 * GIB) == ("11.25G", "12.75G")
    assert caps(2 * GIB) == ("1408M", "1664M")


def test_the_readme_shows_the_unit_install_writes_for_8_gb(host):
    block = re.search(r"```ini\n(.*?)```", README.read_text(), re.S)
    r = invoke("service", "install", "--yes", "--dry-run", *EXAMPLE)
    assert r.exit_code == 0, r.output
    assert block and block.group(1) == r.output


def test_yes_takes_the_defaults_and_writes_the_unit(host):
    r = invoke("service", "install", "--yes", "--user", "svc")
    assert r.exit_code == 0, r.output
    text = (host / "aegis.service").read_text()
    for line in ("User=svc", "MemoryHigh=5.5G", "MemoryMax=6.5G", "KillMode=mixed"):
        assert f"\n{line}\n" in text
    assert f"WorkingDirectory={host.resolve()}\n" in text
    assert "--port 8742\n" in text
    unit = host.resolve() / "aegis.service"
    assert f"sudo install -m 644 {unit} /etc/systemd/system/aegis.service" in r.output
    assert "sudo systemctl daemon-reload" in r.output
    assert "sudo systemctl enable --now aegis.service" in r.output


def test_every_question_is_a_flag(host):
    out = host / "units" / "x.service"
    out.parent.mkdir()
    r = invoke(
        "service", "install", *EXAMPLE, "--memory-high", "3G",
        "--memory-max", "4G", "--output", str(out),
    )  # fmt: skip
    assert r.exit_code == 0, r.output  # no prompt: there is no input to read
    text = out.read_text()
    assert "MemoryHigh=3G\nMemoryMax=4G\n" in text
    assert "enable --now x.service" in r.output


def test_it_asks_with_the_defaults_filled_in(host):
    # user, root, port, executable, MemoryHigh, MemoryMax
    r = invoke("service", "install", input="ops\n\n9000\n\n\n7G\n")
    assert r.exit_code == 0, r.output
    assert "MemoryHigh [5.5G]" in r.output
    text = (host / "aegis.service").read_text()
    assert "User=ops\n" in text and "--port 9000\n" in text
    assert "MemoryHigh=5.5G\nMemoryMax=7G\n" in text


def test_dry_run_writes_nothing(host):
    r = invoke("service", "install", "--yes", "--dry-run")
    assert r.exit_code == 0, r.output
    assert r.output.startswith("[Unit]\n") and "MemoryMax=6.5G" in r.output
    assert not (host / "aegis.service").exists()


def test_a_size_systemd_cannot_read_is_refused(host):
    r = invoke("service", "install", "--yes", "--memory-max", "lots")
    assert r.exit_code == 1
    assert not (host / "aegis.service").exists()


LIVE = """\
[Service]
User=svc
ExecStart=/usr/local/bin/aegis serve --port 8899
Restart=always
RestartSec=3

# /etc/systemd/system/aegis-web.service.d/memory.conf
[Service]
OOMPolicy=continue
MemoryHigh=5905580032
MemoryMax=6.5G
KillMode=control-group
"""


def test_doctor_names_each_setting_a_live_unit_does_differently(host):
    (host / "system").mkdir()
    (host / "system" / "aegis-web.service").write_text(LIVE)
    (host / "system" / "aegis-web.service.bak").write_text(LIVE)
    (host / "system" / "other.service").write_text("[Service]\nExecStart=/bin/true\n")
    found = check(read=lambda f, user: f.read_text())
    # MemoryHigh in bytes is the 5.5G install would write, so it is not named.
    assert [(f.level, f.where, f.message) for f in found] == [
        ("warn", "service.aegis-web.service.KillMode", "KillMode is control-group; aegis service install sets mixed"),
        ("warn", "service.aegis-web.service.OOMScoreAdjust", "OOMScoreAdjust is unset; aegis service install sets -500"),
        ("warn", "service.aegis-web.service.Delegate", "Delegate is unset; aegis service install sets yes"),
    ]  # fmt: skip


def test_doctor_passes_the_unit_install_writes(host):
    (host / "system").mkdir()
    text = render(Unit("u", Path("/r"), 1, "aegis", "/bin", *caps(8 * GIB)))
    (host / "system" / "aegis.service").write_text(text)
    assert [f.level for f in check(read=lambda f, user: f.read_text())] == ["ok"]


def test_aegis_doctor_reports_the_unit_and_says_nothing_without_one(host):
    r = invoke("doctor", "--root", str(host))
    assert "service." not in r.output
    (host / "system").mkdir()
    (host / "system" / "aegis-fixture.service").write_text(LIVE)
    r = invoke("doctor", "--root", str(host))
    assert "service.aegis-fixture.service.KillMode" in r.output
    assert "KillMode is control-group; aegis service install sets mixed" in r.output


def test_install_multi_user_adds_the_capability_lines(host):
    from aegis.users import UNIT_LINES

    plain = invoke("service", "install", "--yes", "--dry-run", *EXAMPLE).output
    r = invoke("service", "install", "--yes", "--dry-run", "--multi-user", *EXAMPLE)
    assert r.exit_code == 0, r.output
    text = r.output
    after_user = text.split("User=aegis\n", 1)[1]
    assert all(line in after_user for line in UNIT_LINES)
    stripped = "\n".join(
        x
        for x in text.splitlines()
        if x not in UNIT_LINES and x not in service.MULTI_USER.splitlines()
    )
    assert stripped + "\n" == plain


def test_install_multi_user_refuses_root(host):
    r = invoke(
        "service", "install", "--yes", "--dry-run", "--multi-user", "--user", "root"
    )
    assert r.exit_code == 1 and "root" in r.output
