"""/whats-new: a guide session through the release notes a person has not
toured (#339). The tests read the repository's CHANGELOG.md, which is what an
editable install tours, and take its releases newest first."""

from __future__ import annotations

import getpass
import json
import re
from pathlib import Path

import pytest

from aegis import changelog_read
from aegis.helpstate import installed
from aegis.ops import OpError

from .test_commands import CONFIG, first_user_md, make_app, send, spawn

REPO = Path(__file__).resolve().parents[1]
RELEASES = re.findall(
    r"^## \[(\d+(?:\.\d+)+)\]",
    (REPO / "CHANGELOG.md").read_text(encoding="utf-8"),
    re.M,
)


def toured_in(md: str) -> list[str]:
    """The releases a guide's prompt carries, in prompt order."""
    return re.findall(r"^## \[([^\]]+)\]", md, re.M)


def record_path(root: Path) -> Path:
    return root / ".aegis" / "state" / "help" / f"{getpass.getuser()}.json"


@pytest.fixture
async def start(tmp_path, fake_claude):
    """Boot an app whose person has the given help record, or none."""
    apps = []

    async def boot(record: dict | None = None):
        assert installed() == RELEASES[0], "these tests tour the newest release"
        (tmp_path / ".aegis.yaml").write_text(CONFIG)
        if record is not None:
            record_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
            record_path(tmp_path).write_text(json.dumps(record))
        app = make_app(tmp_path, fake_claude)
        await app.boot()
        apps.append(app)
        return app

    yield boot
    for app in apps:
        await app.shutdown()


async def state(app) -> dict:
    return await app.registry.call("help.state", {})


async def guide(app) -> dict:
    return await app.registry.call("help.guide", {"kind": "whats_new"})


async def test_the_tour_carries_the_releases_since_the_last_one_newest_last(
    start, tmp_path
):
    app = await start({"tutorial": True, "toured": RELEASES[2]})
    assert (await state(app))["whats_new"] == RELEASES[0]
    r = await guide(app)
    md = await first_user_md(app, r["log_id"])
    assert md.startswith(
        "aegis was upgraded. Below are the release notes since my last tour."
    )
    assert toured_in(md) == [RELEASES[1], RELEASES[0]]
    assert "[Unreleased]" not in md
    assert app.sessions.sessions[r["log_id"]].title == f"What's new in {RELEASES[0]}"
    assert (await state(app))["whats_new"] is None
    assert json.loads(record_path(tmp_path).read_text())["toured"] == RELEASES[0]


async def test_someone_from_before_the_help_system_tours_the_installed_release(start):
    app = await start()
    await spawn(app)  # owns a session, so was here before the record existed
    assert (await state(app))["whats_new"] == RELEASES[0]
    md = await first_user_md(app, (await guide(app))["log_id"])
    assert toured_in(md) == [RELEASES[0]]


async def test_a_new_person_is_not_offered_release_notes(start):
    app = await start()
    assert (await state(app))["whats_new"] is None


async def test_dismissing_the_hint_marks_the_installed_release_toured(start, tmp_path):
    app = await start({"tutorial": True, "toured": RELEASES[1]})
    assert await app.registry.call("help.dismiss", {"what": "whats_new"}) == "noted"
    assert (await state(app))["whats_new"] is None
    assert json.loads(record_path(tmp_path).read_text())["toured"] == RELEASES[0]
    assert not app.sessions.sessions, "dismissing starts nothing"


async def test_an_install_without_its_notes_refuses_and_starts_nothing(
    start, tmp_path, monkeypatch
):
    app = await start({"tutorial": True, "toured": RELEASES[1]})
    monkeypatch.setattr(changelog_read, "PACKAGED", tmp_path / "none.md")
    monkeypatch.setattr(changelog_read, "SOURCE", tmp_path / "none.md")
    with pytest.raises(OpError) as e:
        await guide(app)
    assert e.value.code == "no_release_notes"
    assert not app.sessions.sessions
    assert json.loads(record_path(tmp_path).read_text())["toured"] == RELEASES[1]


async def test_a_typed_whats_new_starts_the_guide_and_leaves_its_session_open(start):
    """App._command ends by closing the session for any aegis command it did
    not match, so a missing branch would archive the tab it was typed in."""
    app = await start({"tutorial": True, "toured": RELEASES[1]})
    lid = await spawn(app)
    r = await send(app, lid, "/whats-new")
    md = await first_user_md(app, r["log_id"])
    assert toured_in(md) == [RELEASES[0]]
    assert lid in app.sessions.sessions and lid not in app.sessions.archived
    guide_cwd = app.sessions.sessions[r["log_id"]].spec.cwd
    assert guide_cwd == app.sessions.sessions[lid].spec.cwd


async def test_typed_again_after_a_tour_it_repeats_the_installed_release(start):
    app = await start({"tutorial": True, "toured": RELEASES[0]})
    assert (await state(app))["whats_new"] is None
    lid = await spawn(app)
    md = await first_user_md(app, (await send(app, lid, "/whats-new"))["log_id"])
    assert toured_in(md) == [RELEASES[0]]
