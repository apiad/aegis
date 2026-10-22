"""Files the server reads and writes for a member are opened as the member
(#555; the spec's section 07). Run with ``make test-multiuser``.

Each test names its files after its own session's log id, since the members
outlive a run."""

from __future__ import annotations

import json
import os
import secrets
import shlex

import httpx
import pytest

from .conftest import CAPS, MEMBERS, sudo, until
from .test_switch import send, spawn

pytestmark = [pytest.mark.slow, pytest.mark.multiuser]

BEN, CARA = MEMBERS


def as_member(user: str, cmd: str) -> str:
    """``cmd`` run by ``user``'s shell, its output."""
    return sudo("-u", user, "sh", "-c", cmd, check=False).stdout


def owner(path: str) -> str:
    return sudo("stat", "-c", "%U", path).stdout.strip()


def private(user: str, text: str) -> str:
    """A mode 0600 file of ``user``'s in its home, named fresh: its name."""
    name = f"only-{secrets.token_hex(4)}.txt"
    as_member(user, f"cd && umask 077 && printf %s {shlex.quote(text)} > {name}")
    return name


async def test_a_members_files_view_is_its_home_read_as_it(mu_server):
    s = mu_server
    name = private(BEN, "ben's")
    listed = await s.call(BEN, "fs.list", {"dir": ""})
    assert name in [e["name"] for e in listed["entries"]]
    assert ".aegis.yaml" not in [e["name"] for e in listed["entries"]]
    read = await s.call(BEN, "fs.read", {"path": name})
    assert read["text"] == "ben's"
    new = f"new-{secrets.token_hex(4)}.txt"
    await s.call(BEN, "fs.write", {"path": new, "content": "x", "base": None})
    assert owner(f"/home/{BEN}/{new}") == BEN
    found = await s.call(BEN, "fs.find", {})
    assert new in [rel for rel, _ in found["files"]]


async def test_a_member_cannot_reach_another_home_through_its_view(mu_server):
    s = mu_server
    theirs = private(CARA, "cara's")
    link = f"to-cara-{secrets.token_hex(4)}"
    as_member(BEN, f"cd && ln -s /home/{CARA}/{theirs} {link}")
    # Python 3.14 resolves the link and finds it leads out of the root;
    # 3.13 raises when ben cannot enter cara's home. Both refuse.
    with pytest.raises(RuntimeError, match="outside|forbidden"):
        await s.call(BEN, "fs.read", {"path": link})
    with pytest.raises(RuntimeError, match="outside|forbidden"):
        await s.call(BEN, "fs.read", {"path": f"../{CARA}/{theirs}"})


async def test_a_members_ticket_serves_its_private_file_with_a_range(mu_server):
    s = mu_server
    name = private(BEN, "0123456789")
    url = (await s.call(BEN, "fs.ticket", {"path": name}))["url"]
    base = f"http://127.0.0.1:{s.port}"
    async with httpx.AsyncClient(base_url=base) as c:
        whole = await c.get(url)
        part = await c.get(url, headers={"range": "bytes=2-4"})
    assert whole.status_code == 200 and whole.text == "0123456789"
    assert part.status_code == 206 and part.text == "234"


async def test_a_members_agent_sends_its_own_file_and_not_anothers(mu_server):
    s = mu_server
    log_id, _ = await spawn(s)
    mine, theirs = private(BEN, "mine"), private(CARA, "theirs")
    await send(
        s,
        BEN,
        log_id,
        f"/mcp file_send {json.dumps({'paths': [f'/home/{BEN}/{mine}']})}",
    )
    stored = await until(
        lambda: list((s.state / "files").glob(f"*/{mine}")), what="the sent file"
    )
    assert stored[0].read_text() == "mine"
    path = f"/home/{CARA}/{theirs}"
    await send(s, BEN, log_id, f"/mcp file_send {json.dumps({'paths': [path]})}")
    transcript = s.state / "transcripts" / f"{log_id}.jsonl"
    await until(
        lambda: (
            path in (t := transcript.read_text())
            and ("not_found" in t or "forbidden" in t)
        ),
        what="the refusal",
    )
    assert not list((s.state / "files").glob(f"*/{theirs}"))


async def test_a_members_attachment_lands_in_its_home_inbox(mu_server):
    import base64

    s = mu_server
    log_id, _ = await spawn(s)
    up = await s.call(
        BEN, "attachment.begin", {"log_id": log_id, "name": "a.txt", "size": 2}
    )
    data = base64.b64encode(b"hi").decode()
    await s.call(
        BEN,
        "attachment.put",
        {"log_id": log_id, "upload_id": up["upload_id"], "offset": 0, "data": data},
    )
    await s.call(
        BEN,
        "session.send",
        {"log_id": log_id, "text": "see this", "attachments": [up["upload_id"]]},
    )
    box = f"/home/{BEN}/.aegis/inbox/{log_id}"
    listed = await until(
        lambda: as_member(BEN, f"ls {box} 2>/dev/null").split(), what="the inbox"
    )
    (name,) = [n for n in listed if n.endswith("-a.txt")]
    assert as_member(BEN, f"cat {box}/{name}") == "hi"
    assert owner(f"{box}/{name}") == BEN


async def test_a_members_artifact_draft_is_in_its_home_and_sends(mu_server):
    s = mu_server
    log_id, _ = await spawn(s)
    drafts = f"/home/{BEN}/.aegis/artifacts"
    before = set(as_member(BEN, f"ls {drafts} 2>/dev/null").split())
    await send(s, BEN, log_id, f"/mcp artifact_create {json.dumps({'title': 'Pick'})}")
    (aid,) = await until(
        lambda: set(as_member(BEN, f"ls {drafts} 2>/dev/null").split()) - before,
        what="the draft",
    )
    page = f"{drafts}/{aid}/index.html"
    assert owner(page) == BEN
    # The agent edits it with its own tools, as the member.
    edit = "<p>edited</p><script>aegis.ready(() => aegis.state({}))</script>"
    as_member(BEN, f"sed -i 's|</body>|{edit}</body>|' {page}")
    await send(s, BEN, log_id, f"/mcp artifact_send {json.dumps({'id': aid})}")
    stored = await until(
        lambda: [p for p in (s.state / "files").glob("*/index.html")
                 if "edited" in p.read_text()],
        what="the sent page",
    )  # fmt: skip
    assert stored


def test_the_runner_check_can_fail(host):
    """Without the switch, a process confined as the server is (its uid, its
    three capabilities) cannot read a member's private file: what the
    runner adds is real. The test's own shell may read more (a CI runner's
    user can), so the check runs under the server's unit settings."""
    name = private(BEN, "x")
    r = sudo(
        "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        f"--uid={os.getuid()}", f"--gid={os.getgid()}",
        "-p", f"AmbientCapabilities={CAPS}", "-p", f"CapabilityBoundingSet={CAPS}",
        "cat", f"/home/{BEN}/{name}",
        check=False,
    )  # fmt: skip
    path = f"/home/{BEN}/{name}"
    why = sudo(
        "sh", "-c",
        f"stat -c '%a %U %G' /home/{BEN} {path}; id; id {BEN}; "
        f"getfacl -p {path} 2>&1 | tail -4; grep -E '^(runner|{BEN}):' /etc/passwd",
        check=False,
    ).stdout  # fmt: skip
    assert r.returncode != 0 and "x" not in r.stdout, (why, os.getuid(), os.getgroups())
