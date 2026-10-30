"""Every process a member starts runs as the member, with no capabilities
(#548; the spec's section 12). Run with ``make test-multiuser``."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess

import pytest

from aegis import restarts, users

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


async def test_a_member_can_close_their_session(mu_server):
    log_id, rep = await spawn(mu_server)
    await mu_server.call(BEN, "session.close", {"log_id": log_id})
    await until(
        lambda: restarts.started_at(rep["pid"]) is None,
        what="the member's harness to stop",
    )


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
        # The pause keeps the first code alone in the file for a while, so
        # a wait that returns on one code fails every run, not one in many.
        f"/sh (touch /home/{CARA}/x 2>/dev/null; echo $?; sleep 1; "
        f"touch {mu_server.state}/x 2>/dev/null; echo $?) > {out}",
    )
    # The subshell writes one code per line, so wait for both, not the first.
    codes = await until(
        lambda: out.exists() and len(c := out.read_text().split()) == 2 and c,
        what="the writes",
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
        "source": None,
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


async def test_no_token_in_a_members_harness_argv(mu_server):
    """Every user can read /proc/<pid>/cmdline: the argv names the variable."""
    _, rep = await spawn(mu_server)
    argv = json.dumps(rep["argv"])
    assert "${AEGIS_SESSION_TOKEN}" in argv
    assert not re.search(r'X-Aegis-Session\\?":\s*\\?"[\w-]{20,}', argv)


async def test_in_multi_user_mode_the_script_runs_as_the_member(mu_server):
    s = mu_server
    folder = s.state / "knowledge" / BEN / "skills" / "who"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: who\ndescription: Say who runs it\n"
        "aegis:\n  scripts: [who.py]\n---\n1. run who.py\n"
    )
    (folder / "who.py").write_text(
        "import os, pwd, sys\n"
        "me = pwd.getpwuid(os.getuid()).pw_name\n"
        "open(os.path.join(sys.argv[1], 'skill'), 'w').write(me + ' ' + os.getcwd())\n"
    )
    log_id, _ = await spawn(s)
    await send(s, BEN, log_id, f"/mcp skill_read {json.dumps({'name': 'who'})}")
    run = {"skill": "who", "script": "who.py", "args": [str(s.out)]}
    await send(s, BEN, log_id, f"/mcp skill_run {json.dumps(run)}")
    f = s.out / "skill"
    said = await until(lambda: f.exists() and f.read_text().strip(), what="the run")
    user, cwd = said.split(" ", 1)
    assert user == BEN and cwd != "/" and "knowledge" not in cwd


def _procs_of(user: str, word: str) -> list[int]:
    """The pids of ``user``'s processes whose argv holds ``word``."""
    import os
    import pwd
    from pathlib import Path

    uid = pwd.getpwnam(user).pw_uid
    found = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            if d.stat().st_uid == uid and word in (d / "cmdline").read_bytes().decode():
                found.append(int(d.name))
        except OSError:
            continue
    return [p for p in found if p != os.getpid()]


async def test_a_members_old_skill_server_is_stopped_as_the_member(mu_server):
    """The server may not signal a member's processes itself, so stopping a
    skill server goes through the launcher as the member: here the old one,
    when the skill changed and the next call starts it again."""
    from pathlib import Path

    s = mu_server
    echo = (Path(__file__).parents[1] / "fixtures" / "skill_mcp_echo.py").read_bytes()
    skill = {
        "name": "echoer",
        "description": "Echo text through a tool",
        "body": "1. echo",
        "aegis": {"mcp": {"command": ["python3", "echo_server.py"]}},
        "files": {"echo_server.py": __import__("base64").b64encode(echo).decode()},
    }
    await s.call(BEN, "skill.save", skill)
    log_id, _ = await spawn(s)
    await send(s, BEN, log_id, f"/mcp skill_read {json.dumps({'name': 'echoer'})}")
    # The shell and python3 under it both name the file.
    old = set(await until(lambda: _procs_of(BEN, "echo_server.py"), what="the server"))
    await s.call(BEN, "skill.save", skill | {"body": "1. echo again"})
    call = {"skill": "echoer", "tool": "echo", "args": {"text": "x"}}
    await send(s, BEN, log_id, f"/mcp skill_tool {json.dumps(call)}")
    await until(
        lambda: (p := set(_procs_of(BEN, "echo_server.py"))) and not p & old,
        what="the old server's end and the new one's start",
    )
