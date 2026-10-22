"""The panel's Repos row: a session's repos and worktrees, live (#526)."""

import subprocess

import pytest

from .test_browser import (  # noqa: F401
    browser,
    page,
    server,
    spawn,
    turns_done,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

G = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(cwd, *args):
    subprocess.run([*G, *args], cwd=cwd, check=True, capture_output=True)


def test_the_repos_row_follows_writes_commits_and_pushes(server, page):  # noqa: F811
    root = server.root
    git(root, "init", "-q", "--bare", "-b", "main", "remote.git")
    repo = root / "proj"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "remote", "add", "origin", str(root / "remote.git"))
    (repo / "a.py").write_text("x\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "first")
    git(repo, "push", "-q", "-u", "origin", "main")
    wt = root / "proj-wt"
    git(repo, "worktree", "add", "-q", "-b", "topic", str(wt))
    peer_wt = root / "proj-peer"
    git(repo, "worktree", "add", "-q", "-b", "peer", str(peer_wt))

    page.goto(server.url)
    # A peer that wrote in two checkouts of the same repo (#595).
    spawn(page, f"/write {repo}/p.py => peer")
    page.fill("#input", f"/write {peer_wt}/q.py => peer")
    page.press("#input", "Enter")
    turns_done(page, 2)

    spawn(page, f"/write {repo}/b.py => one")
    lines = page.locator("#s-repos .rp-line")
    lines.first.wait_for(timeout=10000)
    assert "proj" in lines.first.inner_text() and "~1" in lines.first.inner_text()

    page.fill("#input", f"/write {wt}/c.py => two")
    page.press("#input", "Enter")
    turns_done(page, 2)
    # Each repo is one line, its checkouts summed in it.
    page.wait_for_function(
        "[...document.querySelectorAll('#s-repos .rp-line')]"
        ".some(l => l.textContent.includes('2 checkouts') && l.textContent.includes('~2'))",
        timeout=15000,
    )
    assert lines.count() == 1

    page.fill(
        "#input",
        f"/sh cd {repo} && git add b.py && {' '.join(G)} commit -m 'add b'"
        " && git push -q",
    )
    page.press("#input", "Enter")
    turns_done(page, 3)
    page.hover("#p-repos")
    card = page.locator("#p-repos .pcard")
    card.wait_for(state="visible")
    # The commit's probe may wait out the 10 s window of the write's.
    page.wait_for_function(
        "document.querySelector('#s-repos-all').textContent.includes('clean, pushed')",
        timeout=20000,
    )
    text = card.inner_text()
    assert "add b" in text and "main checkout" in text and "worktree proj-wt" in text
    assert card.locator("h5.rp-h").count() == 1
    # The peer is named once, with both its checkouts under it.
    assert text.count("is also here") == 1
    assert "worktree proj-peer" in text
    assert page.errors == []
