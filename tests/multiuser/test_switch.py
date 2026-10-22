"""Every process a member starts runs as the member, with no capabilities
(#548; the spec's section 12). Run with ``make test-multiuser``."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess

import pytest

from aegis import users

from .conftest import CAPS, MEMBERS, until

pytestmark = [pytest.mark.slow, pytest.mark.multiuser]

BEN, CARA = MEMBERS


async def spawn(s, who: str = BEN) -> tuple[str, dict]:
    """A session of ``who``'s, and what its harness reported."""
    await s.call(
        "admin", "token.policy", {"name": "*", "policy": {"claude": {"mode": "open"}}}
    )
    r = await s.call(who, "session.spawn", {"agent": "opus"})
    (rep, *_) = await until(lambda: s.harness(who), what="the harness report")
    return r["log_id"], rep


async def send(s, who: str, log_id: str, text: str) -> None:
    await s.call(who, "session.send", {"log_id": log_id, "text": text})


async def test_a_members_harness_runs_as_the_member_in_its_home(mu_server):
    _, rep = await spawn(mu_server)
    assert rep["user"] == BEN and rep["home"] == f"/home/{BEN}"


async def test_a_members_harness_holds_no_capabilities(mu_server):
    _, rep = await spawn(mu_server)
    st = rep["status"]
    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        assert int(st[key], 16) == 0, (key, st[key])
    assert st["NoNewPrivs"] == "1"


def test_the_same_check_fails_when_the_bounding_set_is_kept(host):
    """The capabilities test can fail: without the bounding drop, a child
    under the same unit settings keeps a bounding set."""
    argv = [a for a in users.setpriv_argv(None) if a != "--bounding-set=-all"]
    r = subprocess.run(
        ["sudo", "-n", "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
         f"--uid={__import__('os').getuid()}",
         "-p", f"AmbientCapabilities={CAPS}", "-p", f"CapabilityBoundingSet={CAPS}",
         *argv, "grep", "CapBnd", "/proc/self/status"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert int(r.stdout.split()[-1], 16) != 0


async def test_the_pool_reaches_the_member_and_the_servers_env_does_not(mu_server):
    _, rep = await spawn(mu_server)
    assert rep["pool"] == "from-pool"
    assert rep["server_only"] is None


async def test_the_recorded_pid_is_the_harness_and_sits_in_its_cgroup(mu_server):
    log_id, rep = await spawn(mu_server)
    want = f'"pid": {rep["pid"]}'
    seen = await mu_server.watch(BEN, "sessions", lambda m: want in json.dumps(m))
    assert log_id in json.dumps(seen)
    assert log_id in rep["cgroup"]


async def test_a_member_cannot_write_another_home_or_the_state(mu_server):
    log_id, _ = await spawn(mu_server)
    out = mu_server.out / "writes"
    await send(
        mu_server,
        BEN,
        log_id,
        f"/sh (touch /home/{CARA}/x 2>/dev/null; echo $?; "
        f"touch {mu_server.state}/x 2>/dev/null; echo $?) > {out}",
    )
    codes = await until(
        lambda: out.exists() and out.read_text().split(), what="the writes"
    )
    assert codes[0] != "0" and codes[1] != "0"


async def test_a_terminal_a_monitor_a_workflow_step_and_a_queue_worker_run_as_the_owner(
    mu_server,
):
    s = mu_server
    log_id, _ = await spawn(s)
    out = s.out
    # A terminal: its shell, as the member.
    await s.call(BEN, "term.spawn", {"profile": "shell"})
    # A monitor: its done check, as the member.
    monitor = {
        "description": "who",
        "done": f"id -un > {out}/monitor",
        "progress": None,
        "timeout_s": 60,
    }
    await send(s, BEN, log_id, f"/mcp monitor_start {json.dumps(monitor)}")
    # A workflow sh step, approved by its owner.
    flow = (
        "id: who\ntitle: Who\nsay: Who.\nroot:\n  seq:\n"
        f'    - {{sh: "id -un > {out}/workflow", say: Who runs it.}}\n'
        "  say: Done.\n"
    )
    await send(
        s,
        BEN,
        log_id,
        f"/mcp workflow_arm {json.dumps({'definition': flow, 'args': {}})}",
    )
    transcript = s.state / "transcripts" / f"{log_id}.jsonl"
    run = await until(
        lambda: (
            transcript.exists()
            and (m := re.search(r'\\?"run\\?":\s*\\?"([\w-]+)', transcript.read_text()))
            and m.group(1)
        ),
        what="the armed run",
    )
    await s.call(BEN, "workflow.approve", {"run": run})
    # A queue worker: a second harness of the member's.
    task = {"queue": "general", "payload": "say hi"}
    await send(s, BEN, log_id, f"/mcp queue_enqueue {json.dumps(task)}")
    for name in ("monitor", "workflow"):
        f = out / name
        assert (
            await until(lambda: f.exists() and f.read_text().strip(), what=name) == BEN
        )
    await until(lambda: len(s.harness(BEN)) >= 2, what="the queue worker")
    assert {r["user"] for r in s.reports() if "--version" not in r["argv"]} == {BEN}


async def test_the_repos_panel_runs_git_as_the_member(mu_server):
    """A repository the server's user cannot read still shows its branch."""
    s = mu_server
    log_id, _ = await spawn(s)
    repo = f"/home/{BEN}/r-{log_id}"  # members outlive a run
    await send(
        s,
        BEN,
        log_id,
        f"/sh mkdir -m 700 {repo} && cd {repo} && git init -q -b ben-branch && "
        "git -c user.name=b -c user.email=b@x commit -q --allow-empty -m one && "
        "echo hi > f.txt",
    )
    await send(s, BEN, log_id, f"/write {repo}/g.txt => written")
    seen = await s.watch(
        BEN, f"repos:{log_id}", lambda m: "ben-branch" in json.dumps(m), 60
    )
    assert "ben-branch" in json.dumps(seen)


async def test_the_journal_names_a_members_commit(mu_server):
    s = mu_server
    log_id, _ = await spawn(s)
    repo = f"/home/{BEN}/j-{log_id}"
    await send(
        s,
        BEN,
        log_id,
        f"/sh mkdir -m 700 {repo} && cd {repo} && git init -q && echo a > a.txt && "
        "git add a.txt && git -c user.name=b -c user.email=b@x commit -m journal-me "
        f"2>&1 | tee {s.out}/jlog",
    )

    async def found():
        r = await s.call(BEN, "journal.search", {})
        return [x for x in json.dumps(r).split("},") if "journal-me" in x and repo in x]

    end = asyncio.get_running_loop().time() + 30
    hits = []
    while not hits and asyncio.get_running_loop().time() < end:
        hits = await found()
        await asyncio.sleep(0.5)
    jlog = s.out / "jlog"
    assert hits, (
        jlog.read_text() if jlog.exists() else "no jlog",
        json.dumps(await s.call(BEN, "journal.search", {}))[-800:],
    )


@pytest.mark.browser
def test_settings_asks_for_the_linux_user_in_multi_user_mode(mu_server):
    from playwright.sync_api import sync_playwright

    s = mu_server
    from tests.conftest import browser_slot

    with browser_slot(), sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={"width": 1280, "height": 800})
            pg.goto(f"http://127.0.0.1:{s.port}/?token={s.tokens['admin']}")
            pg.click("#settings-btn")
            pg.wait_for_selector("#set-tokens .set-uid")
            pg.fill("#set-token-name", "mate")
            assert pg.input_value("#set-token-user") == "mate"
            pg.fill("#set-token-user", CARA)
            pg.click("#set-token-add")
            pg.wait_for_selector("#set-token-once")
            card = pg.wait_for_selector("#set-tokens .tok-mate")
            assert f"uid {CARA}" in card.text_content()
        finally:
            b.close()
