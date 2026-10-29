"""aegis usage import on a shared server: each member's Claude Code files are
read as that member and counted as its sessions (#737)."""

import json
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta

from .conftest import MEMBERS, sudo

BEN, CARA = MEMBERS
SID = "0b1c2d3e-0000-4000-8000-0000000000b1"


def _lines(sid: str) -> str:
    # A day ago, so the rows stay inside usage.read's default window ("4w").
    at = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        {"type": "user", "message": {"role": "user", "content": "go"}},
        {
            "type": "assistant",
            "message": {
                "id": f"m-{sid}",
                "model": "claude-opus-4-7",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        },
    ]
    return "".join(
        json.dumps({**r, "timestamp": at, "cwd": "/tmp", "sessionId": sid}) + "\n"
        for r in rows
    )


def _write_as(user: str, rel: str, text: str) -> str:
    path = f"/home/{user}/.claude/projects/-tmp/{rel}"
    subprocess.run(
        ["sudo", "-n", "-u", user, "sh", "-c", "mkdir -p ${0%/*} && cat > $0", path],
        input=text,
        text=True,
        check=True,
    )
    return path


def _imported(state) -> list[tuple[str, str]]:
    con = sqlite3.connect(f"file:{state / 'journal.db'}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT DISTINCT person, log_id FROM usage_turns"
            " WHERE log_id LIKE 'cc-%' ORDER BY person"
        ).fetchall()
    finally:
        con.close()


async def test_the_import_reads_each_members_files_as_it(mu_server):
    s = mu_server
    _write_as(BEN, f"{SID}.jsonl", _lines(SID))
    cara_sid = SID[:-2] + "c1"
    _write_as(CARA, f"{cara_sid}.jsonl", _lines(cara_sid))
    # A file in ben's tree that ben cannot read is skipped and listed.
    locked = _write_as(BEN, "locked.jsonl", _lines("locked"))
    sudo("chown", "root:root", locked)
    sudo("chmod", "0600", locked)
    try:
        r = await s.call("admin", "usage.import", {"people": [BEN, CARA]})
        assert r["sessions"] == 2
        assert r["unreadable"] == [locked]
        assert _imported(s.state) == [(BEN, f"cc-{SID}"), (CARA, f"cc-{cara_sid}")]
        again = await s.call("admin", "usage.import", {"people": [BEN, CARA]})
        assert (again["sessions"], again["already"]) == (0, 2)
        mine = await s.call(BEN, "usage.read", {"split": ["session"]})
        assert {row["session"] for row in mine["rows"]} == {f"cc-{SID}"}
    finally:
        for user in (BEN, CARA):
            sudo("rm", "-rf", f"/home/{user}/.claude")


async def test_a_member_cannot_import(mu_server):
    try:
        await mu_server.call(BEN, "usage.import", {})
    except RuntimeError as e:
        assert "usage.import" in str(e)
    else:
        raise AssertionError("a member imported")
