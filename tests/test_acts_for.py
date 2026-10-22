"""Who each process acts for, in multi-user mode (#548): every call site passes
the Linux user of the person it works for to the launcher.

The user switch itself needs capabilities and real users (tests/multiuser/).
Here the mode is on for the app and its tokens, the launcher's own switch is
off, and a recorder notes the ``user`` each start asks for. A member ``ben``
is a fake account whose home is under the test's tmp_path.
"""

import json
import pwd

import pytest

from aegis import launch, users
from aegis.ops import Caller, Principal
from aegis.users import MultiUser

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import give_up, ok
from .test_workflows_steps import flow

BEN = Caller("user", principal=Principal("ben", "member"))


class Recorded(list):
    cwds: list

    def users(self, shape: str | None = None) -> set:
        return {u for s, argv, u in self if shape is None or s == shape}

    def harness(self, bin: str) -> set:
        """The users of the harness starts; the version probe is the server's."""
        return {u for s, argv, u in self if argv[0] == bin and "--version" not in argv}

    def of(self, word: str) -> set:
        """The users of the starts whose argv mentions ``word``."""
        return {u for s, argv, u in self if any(word in str(a) for a in argv)}


@pytest.fixture
def recorded(monkeypatch):
    """Every start's shape, argv and user, started for real as the server."""
    seen = Recorded()
    seen.cwds = []

    def spy(shape, real):
        if shape in ("child", "pty"):

            async def f(argv, *, user, **kw):
                seen.append((shape, list(argv), user))
                seen.cwds.append(str(kw.get("cwd")))
                return await real(argv, user=None, **kw)

        else:

            def f(argv, *, user, **kw):
                seen.append((shape, list(argv), user))
                return real(argv, user=None, **kw)

        return f

    for shape in ("child", "pty", "run"):
        monkeypatch.setattr(launch, shape, spy(shape, getattr(launch, shape)))
    return seen


@pytest.fixture
def ben_home(tmp_path, monkeypatch):
    home = tmp_path / "home-ben"
    home.mkdir()
    ben = pwd.struct_passwd(("ben", "x", 1500, 1500, "", str(home), "/bin/sh"))
    real = pwd.getpwnam
    monkeypatch.setattr(pwd, "getpwnam", lambda n: ben if n == "ben" else real(n))
    monkeypatch.setattr(users, "allowed", lambda name, **kw: ben)
    return home


async def mu_world(tmp_path, fake_claude, monkeypatch, text: str = CONFIG):
    """A world whose app runs in multi-user mode, with a member ben."""
    (tmp_path / ".aegis.yaml").write_text(text + "multi_user:\n")
    from aegis import app as app_mod

    real = app_mod.App.__init__

    def init(self, *a, **kw):
        real(self, *a, **{**kw, "multi_user": MultiUser()})
        # The launcher's own switch needs capabilities: tests/multiuser/.
        launch.configure(None)

    monkeypatch.setattr(app_mod.App, "__init__", init)
    w = await World(tmp_path, fake_claude).start()
    w.app.principals.add("ben", "member")
    w.app.principals.set_default_policy({"claude": {"mode": "open"}})
    return w


@pytest.fixture
async def world(tmp_path, fake_claude, monkeypatch, ben_home, recorded):
    w = await mu_world(tmp_path, fake_claude, monkeypatch)
    yield w
    await w.stop()


def meta(world, log_id: str) -> dict:
    world.app.sessions.metas.flush()
    return json.loads(world.app.sessions.metas.path(log_id).read_text())


async def ben_spawn(world, **params):
    r = await world.app.registry.call("session.spawn", {"agent": "opus", **params}, BEN)
    return world.app.sessions.sessions[r["log_id"]]


async def test_a_members_session_records_its_user_and_starts_in_its_home(
    world, recorded, ben_home
):
    s = await ben_spawn(world)
    assert s.spec.linux_user == "ben"
    assert s.spec.cwd == ben_home
    assert meta(world, s.log_id)["linux_user"] == "ben"
    assert recorded.harness(world.fake) == {"ben"}


async def test_an_admins_session_keeps_the_root_and_runs_as_the_server(world, recorded):
    s = await world.spawn()
    assert s.spec.linux_user is None and s.spec.cwd == world.root
    assert recorded.harness(world.fake) == {None}


async def test_a_member_session_from_before_the_mode_fails_to_start(world, recorded):
    s = await ben_spawn(world)
    await s.stop()
    s.spec = s.spec.__class__(**{**s.spec.__dict__, "linux_user": None})
    recorded.clear()
    with pytest.raises(PermissionError, match="created before multi-user mode"):
        await s.start()
    assert recorded == []


async def test_the_recorded_user_outlives_a_change_of_the_tokens_user(
    world, recorded, monkeypatch
):
    s = await ben_spawn(world)
    monkeypatch.setattr(world.app.principals, "linux_user_of", lambda n: "cara")
    await s.stop()
    recorded.clear()
    await s.start()
    assert recorded.harness(world.fake) == {"ben"}


async def test_a_members_terminal_runs_as_the_member_in_its_home(
    world, recorded, ben_home
):
    r = await world.app.registry.call("term.spawn", {"profile": "shell"}, BEN)
    assert recorded.users("pty") == {"ben"}
    meta = world.app.terminals.get(r["id"]).meta
    assert meta["linux_user"] == "ben" and meta["cwd"] == str(ben_home)
    assert recorded[-1][1][-2:] == ["/bin/sh", "-l"]  # ben's shell, from passwd


async def test_a_monitor_runs_as_the_owner_of_the_arming_session(world, recorded):
    s = await ben_spawn(world)
    await world.app.registry.call(
        "monitor.start",
        {"description": "w", "done": "echo monitor-ran", "progress": None},
        Caller("agent", log_id=s.log_id),
    )
    await until(lambda: recorded.of("monitor-ran"), timeout=10, what="the check")
    assert recorded.of("monitor-ran") == {"ben"}


async def test_a_schedule_check_runs_as_the_person_testing_it(world, recorded):
    await world.app.registry.call(
        "schedule.test",
        {"clock": {"every": "5m"}, "check": {"bash": "echo sched-ran"}},
        BEN,
    )
    assert recorded.of("sched-ran") == {"ben"}


async def test_a_workflow_sh_step_and_probe_run_as_the_workflows_owner(world, recorded):
    main = await ben_spawn(world)
    armed = ok(
        await turn(
            main,
            mcp(
                "workflow_arm",
                definition=flow(
                    '{sh: "echo step-ran", say: Step.}',
                    '{check: {sh: "echo probe-ran"}, say: Probe.}',
                ),
                args={},
            ),
        )
    )
    await world.app.registry.call("workflow.approve", {"run": armed["run"]})
    await give_up(world, armed["run"], 30)
    assert recorded.of("step-ran") == {"ben"}
    assert recorded.of("probe-ran") == {"ben"}


async def test_a_queue_worker_runs_as_its_enqueuers_user(world, recorded):
    main = await ben_spawn(world)
    await turn(main, mcp("queue_enqueue", queue="general", payload="say hi"))
    await until(
        lambda: len(world.app.sessions.sessions) >= 2, timeout=10, what="the worker"
    )
    worker = next(s for s in world.app.sessions.sessions.values() if s is not main)
    assert worker.spec.linux_user == "ben"


async def test_outside_the_mode_every_site_passes_none(tmp_path, fake_claude, recorded):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    try:
        s = await w.spawn()
        await w.app.registry.call("term.spawn", {"profile": "shell"})
        assert s.spec.linux_user is None
        assert "linux_user" not in meta(w, s.log_id)
        assert recorded.users() == {None}
    finally:
        await w.stop()


async def test_a_recap_runs_as_the_summarised_sessions_owner(
    tmp_path, fake_claude, monkeypatch, ben_home, recorded
):
    w = await mu_world(
        tmp_path, fake_claude, monkeypatch, CONFIG + "recap: {agent: opus}\n"
    )
    try:
        s = await ben_spawn(w)
        await turn(s, "first ask")
        await turn(s, "second ask?")
        recorded.clear()
        await w.app.registry.call("recap.request", {"log_id": s.log_id})
        assert recorded.users("child") == {"ben"}
        assert str(w.app.roots.state_root / "oneshot") not in recorded.cwds
    finally:
        await w.stop()


async def test_a_btw_runs_as_the_sessions_owner(
    tmp_path, fake_claude, monkeypatch, ben_home, recorded
):
    w = await mu_world(
        tmp_path, fake_claude, monkeypatch, CONFIG + "btw: {agent: opus}\n"
    )
    try:
        s = await ben_spawn(w)
        recorded.clear()
        await w.app.registry.call(
            "session.send", {"log_id": s.log_id, "text": "/btw what?"}, BEN
        )
        await until(lambda: recorded.users("child"), timeout=8, what="the btw call")
        assert recorded.users("child") == {"ben"}
    finally:
        await w.stop()


def test_place_sends_a_member_at_the_root_to_its_home(tmp_path, ben_home):
    from aegis.session import SpawnSpec

    spec = SpawnSpec(
        agent="a", model="m", effort="high", permission="full", cwd=tmp_path
    )
    out = users.place(spec, root=tmp_path, role="member", linux_user="ben")
    assert (out.linux_user, out.cwd) == ("ben", ben_home)
    other = tmp_path / "elsewhere"
    spec2 = SpawnSpec(agent="a", model="m", effort="high", permission="full", cwd=other)
    assert (
        users.place(spec2, root=tmp_path, role="member", linux_user="ben").cwd == other
    )
    admin = users.place(spec, root=tmp_path, role="admin", linux_user="ben")
    assert admin.cwd == tmp_path and admin.linux_user == "ben"


def test_user_for_refuses_a_members_session_without_a_user(tmp_path):
    from aegis.registry import Registry
    from aegis.session import SpawnSpec

    spec = SpawnSpec(
        agent="a",
        model="m",
        effort="high",
        permission="full",
        cwd=tmp_path,
        owner="ben",
    )
    reg = Registry.__new__(Registry)
    reg.multi_user = True
    reg.role_of = lambda n: "member"
    with pytest.raises(PermissionError, match="created before multi-user mode"):
        reg.user_for(spec)
    reg.multi_user = False
    assert reg.user_for(spec) is None


def git_repo(path):
    import subprocess

    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", "one"],
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x",
            "PATH": "/usr/bin:/bin",
        },
    )
    return path


def test_the_journals_git_runs_as_the_given_user(tmp_path, recorded):
    from aegis.journal import paths

    repo = git_repo(tmp_path / "r")
    paths._checkout_cache.clear()
    assert paths.place(str(repo / "f.txt"), user="ben")[2] == str(repo.resolve())
    sha = paths.full_sha(str(repo), "HEAD", user="ben")
    assert sha and paths.commit_files(str(repo), sha, user="ben") == []
    assert recorded.users("run") == {"ben"}


def test_the_journal_asks_its_sessions_user(tmp_path, monkeypatch):
    from aegis.journal import paths
    from aegis.journal.derive import Row
    from aegis.journal.service import Journal

    seen = []

    def place(p, user=None):
        seen.append(user)
        return None, p, None

    monkeypatch.setattr(paths, "place", place)
    j = Journal(tmp_path, lambda *a: None, user_of=lambda lid: "ben")
    row = Row(1, 0.0, "turn", "", "h", touches=[("write", "/x")], workdir="/w")
    j._entry("L1", 0, row)
    assert seen and set(seen) == {"ben"}


async def test_the_repos_panel_probes_as_the_sessions_user(tmp_path, recorded):
    from aegis.repos import gh
    from aegis.repos.service import Repos

    repo = git_repo(tmp_path / "r")
    r = Repos(
        tmp_path / "j.db",
        lambda *a: None,
        lambda: {},
        lambda name: 0,
        tmp_path,
        user_of=lambda lid: "ben",
    )
    r._on_live("L1", [], [str(repo)])
    await r._probe_and_refresh(str(repo))
    assert recorded.users("run") == {"ben"}
    recorded.clear()
    gh.prs(str(repo), "main", user="ben")
    assert recorded.users("run") <= {"ben"}


def test_a_repo_the_server_cannot_see_is_found_through_its_user(
    tmp_path, recorded, monkeypatch
):
    """The server may not stat inside a member's home: with a user, git run
    as that user says whether the directory and the repo are there."""
    import os

    from aegis.journal import paths
    from aegis.repos import probe

    repo = git_repo(tmp_path / "r")
    (repo / "f.txt").write_text("x")
    paths._checkout_cache.clear()
    monkeypatch.setattr(os.path, "isdir", lambda p: False)
    main, rel, top = paths.place(str(repo / "f.txt"), user="ben")
    assert (rel, top) == ("f.txt", str(repo.resolve()))
    assert paths.place(str(repo), user="ben")[2] == str(repo.resolve())
    assert probe.probe(str(repo), user="ben") is not None
    assert paths.place(str(repo / "f.txt"))[2] is None  # the server's own view
