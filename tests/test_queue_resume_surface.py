"""The surfaces a person or an agent uses to act on a parked worker.

Three planes, and the split between them is not arbitrary. The daemon's
unix socket is a view-attachment stream, not request/response RPC, so only
the two surfaces already bound to a live brain can ACT — MCP
(`aegis_task_resume` / `aegis_task_retry`) and the TUI's slash commands
(`/queues tasks`, `/resume`). `aegis queue` is a separate process reading
the JSONL log, so it can only READ.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from aegis.commands import CommandContext, dispatch
from tests.conftest import make_queue_rig, worker_handle


# --------------------------------------------------------------------------
# QueueManager.resume_task — the brief's five
# --------------------------------------------------------------------------


async def test_resume_puts_a_parked_worker_back_to_work(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    res = await qm.resume_task(tid)
    assert res["ok"] is True
    assert qm.status(tid)["status"] == "dispatched"
    assert sm.get(handle).origin.kind == "queue"


async def test_resume_resets_the_attempt_budget(parked_rig):
    """The operator looked at it and said go. That is new information, and
    charging the new run for the old run's failures would park it again
    immediately."""
    qm, sm, tid, handle, _ = parked_rig
    await qm.resume_task(tid)
    assert qm._all[tid].attempts == 0


async def test_resume_on_an_unknown_task_returns_an_error(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    assert (await qm.resume_task("nope"))["ok"] is False


async def test_resume_with_no_recorded_conversation_fails_gracefully(parked_rig):
    """The one genuine dead end: nothing under the handle AND no recorded
    conversation id, so there is nothing to spawn `--resume` against. That
    is an error dict, not a traceback out of an MCP tool.

    A closed tab on its own is NOT this case any more — see
    `test_resume_respawns_the_recorded_conversation_after_a_restart`."""
    qm, sm, tid, handle, _ = parked_rig
    await sm.close(handle)
    qm._all[tid] = replace(qm._all[tid], resumable=None)
    res = await qm.resume_task(tid)
    assert res["ok"] is False
    assert "no longer live" in res["error"]


async def test_resume_respawns_a_parked_session_whose_id_is_none(tmp_path):
    """The flagship failure, on default config.

    A worker stalls, `rebuild` succeeds, and the nudge turn dies at
    `start()` — a 403, a TLS blip, a dropped tunnel. `attempts` hits
    `max_attempts` and the task parks, but `AgentSession.adopt` installed a
    fresh driver and both drivers start `session_id` at `None`, so the
    parked session cannot say what conversation it is. `rebuild` reads the
    id LIVE off the session, so a resume called `reconnect`, `reconnect`
    raised "has no session id to resume from", `recovery.rebuild` swallowed
    it, and the operator got "could not rebuild the harness" against a
    conversation that was sitting on the record the whole time.
    """
    qm, sm = make_queue_rig(tmp_path, max_attempts=2)
    tid, _ = qm.enqueue("impl", "go", enqueued_by="agent:producer",
                        callback=True)
    handle = worker_handle(qm, tid)
    sm.emit_system_init(handle, session_id="sess-1")
    await sm.fail(handle, text="halfway")      # stalls; adopt() nulls the id
    await sm.fail(handle, text="halfway")      # budget spent; parks
    assert qm.status(tid)["status"] == "recoverable"
    assert sm.get(handle) is not None, "parking keeps the session"
    assert sm.get(handle).session_id is None, "the rebuilt driver never ran"

    res = await qm.resume_task(tid)

    assert res["ok"] is True, res.get("error")
    assert qm.status(tid)["status"] == "dispatched"
    # From the RECORD, not from the session that could not supply it.
    assert sm.resumed_from == "sess-1"
    # The empty session had to go first, or `restore` takes its own warm
    # branch and fails on the same missing id.
    assert handle in sm.closed
    assert sm.get(handle) is not None, "and a new one stands under the handle"


async def test_resume_respawns_the_recorded_conversation_after_a_restart(
    parked_rig,
):
    """The headless-restart deviation, closed by the same fallback.

    A parked session does not survive a headless restart, and
    `recoverable_ttl_s` defaults to a day — so a restart inside the window
    the deadline was built for (idle reap, an upgrade, a reboot) is routine.
    Refusing there meant the outcome of parking was usually decided by a
    restart rather than by the deadline. Nothing under the handle is not a
    dead end while the record holds a conversation id.
    """
    qm, sm, tid, handle, _ = parked_rig
    await sm.close(handle)                      # the restart, in one line
    assert sm.get(handle) is None

    res = await qm.resume_task(tid)

    assert res["ok"] is True, res.get("error")
    assert res["worker_handle"] == handle
    assert qm.status(tid)["status"] == "dispatched"
    assert sm.resumed_from == "sess-1"
    # Re-origined back to `queue` so the finalizer owns it again, and
    # observed, or the resumed worker would run and never be heard. The
    # origin is read off the spawn call rather than off the session: the
    # stub builds a bare AgentSession and never applies it, where
    # `SessionManager._sync_spawn` stamps it.
    assert sm.spawned_origin.kind == "queue"
    assert sm.spawned_origin.by == "impl"
    assert qm._workers[handle][0].id == tid
    bodies = [m.body for m in sm.inbox_for(handle)]
    assert any("operator has put you back to work" in b for b in bodies)


async def test_resume_on_a_completed_task_is_refused(tmp_path):
    qm, sm = make_queue_rig(tmp_path)
    tid, _ = qm.enqueue("impl", "go", enqueued_by="agent:producer")
    await sm.finish(worker_handle(qm, tid), text="done")
    assert qm.status(tid)["status"] == "completed"
    res = await qm.resume_task(tid)
    assert res["ok"] is False


# --------------------------------------------------------------------------
# resume: the rest of the contract
# --------------------------------------------------------------------------


async def test_resume_rebuilds_the_harness_and_nudges_the_worker(parked_rig):
    """A resume that only flipped a status would leave the operator
    watching a tab with a dead harness under it."""
    from aegis.core.recovery import NUDGE_OPERATOR

    qm, sm, tid, handle, _ = parked_rig
    await qm.resume_task(tid)
    assert handle in sm.reconnected
    assert any(NUDGE_OPERATOR in m.body for m in sm.inbox_for(handle))


async def test_resume_takes_its_slot_back(parked_rig):
    """Parking freed the slot on the way in. Resuming has to take one back,
    or the queue runs max_parallel + 1 workers against the provider."""
    qm, sm, tid, handle, _ = parked_rig
    other, _ = qm.enqueue("impl", "next", enqueued_by="agent:producer")
    assert qm.status(other)["status"] == "dispatched"  # took the freed slot
    res = await qm.resume_task(tid)
    assert res["ok"] is False
    assert "max_parallel" in res["error"]
    assert qm.status(tid)["status"] == "recoverable"


async def test_a_resumed_worker_that_stalls_again_parks_again(parked_rig):
    """The budget still bounds it; the reset is a fresh budget, not none."""
    qm, sm, tid, handle, _ = parked_rig
    await qm.resume_task(tid)
    await sm.fail(handle, text="still stuck")
    assert qm.status(tid)["status"] == "recoverable"


async def test_a_resumed_worker_can_finish_the_task(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    await qm.resume_task(tid)
    await sm.finish(handle, text="finished after all")
    assert qm.status(tid)["status"] == "completed"
    assert qm.status(tid)["result"] == "finished after all"


# --------------------------------------------------------------------------
# retry_task — the explicit second execution
# --------------------------------------------------------------------------


async def test_retry_enqueues_a_new_task_from_the_payload(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    res = await qm.retry_task(tid)
    assert res["ok"] is True
    assert res["task_id"] != tid
    assert qm._all[res["task_id"]].payload == qm._all[tid].payload


async def test_retry_closes_the_parked_session_and_ends_the_old_task(parked_rig):
    """Retry is the statement that this conversation is not worth resuming.
    Leaving it parked would offer a resume competing with the retry, and
    would keep the TTL reaper watching a session nobody wants."""
    qm, sm, tid, handle, _ = parked_rig
    await qm.retry_task(tid)
    assert handle in sm.closed
    assert qm.status(tid)["status"] == "failed"
    assert (await qm.resume_task(tid))["ok"] is False


async def test_retry_on_an_in_flight_task_is_refused(tmp_path):
    """A second execution of work that is still running is the worst case
    of all: two workers in the same tree, neither knowing about the other."""
    qm, sm = make_queue_rig(tmp_path)
    tid, _ = qm.enqueue("impl", "go", enqueued_by="agent:producer")
    assert qm.status(tid)["status"] == "dispatched"
    res = await qm.retry_task(tid)
    assert res["ok"] is False
    assert "cancel it first" in res["error"]


async def test_retry_on_an_unknown_task_returns_an_error(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    assert (await qm.retry_task("nope"))["ok"] is False


async def test_resume_never_falls_back_to_re_running(parked_rig):
    """A worker that got halfway may already have committed, pushed,
    deployed or sent mail, so resume never re-dispatches the payload.

    With nothing under the handle it respawns the RECORDED CONVERSATION —
    `resume_from` carries the session id, and the payload is not sent as an
    opening prompt. Same task id either way: a new one in `_all` would be
    `retry_task`, which a caller asks for by name."""
    qm, sm, tid, handle, _ = parked_rig
    await sm.close(handle)
    before = set(qm._all)

    assert (await qm.resume_task(tid))["ok"] is True

    assert set(qm._all) == before, "a resume never creates a second task"
    assert sm.resumed_from == "sess-1", "spawned against the conversation"
    assert qm._all[tid].payload not in [
        m.body for m in sm.inbox_for(handle)
    ], "the payload was not re-sent"


async def test_resume_with_no_conversation_recorded_never_re_runs(parked_rig):
    """And when there is no conversation to respawn either, it refuses
    rather than spawning a fresh agent on the payload."""
    qm, sm, tid, handle, _ = parked_rig
    await sm.close(handle)
    qm._all[tid] = replace(qm._all[tid], resumable=None)
    before = set(qm._all)

    assert (await qm.resume_task(tid))["ok"] is False

    assert set(qm._all) == before
    assert sm.spawned == [handle]  # the original dispatch, and nothing since


# --------------------------------------------------------------------------
# status() carries the handle (carry-over C)
# --------------------------------------------------------------------------


async def test_status_names_the_parked_worker(parked_rig):
    """A producer that enqueued with callback=False and polls otherwise
    gets a `recoverable` it cannot act on: it can neither read the parked
    conversation nor say which session to resume."""
    qm, sm, tid, handle, _ = parked_rig
    assert qm.status(tid)["worker_handle"] == handle


# --------------------------------------------------------------------------
# The park record keeps the worker's last words (carry-over A)
# --------------------------------------------------------------------------


async def test_a_parked_workers_last_words_survive_two_restarts(tmp_path):
    """`_task_from_record` reads `recoverable` as terminal, so it takes
    `result` off the record and never looks at `last_text`. With no
    `result` on the park record the words survived one replay — the
    `deferred` record still had them — and were gone after the second, by
    which time the TTL reaper may have taken the conversation and those
    words were the only thing left of it.
    """
    qm, sm = make_queue_rig(tmp_path, max_attempts=1)
    tid, _ = qm.enqueue("impl", "go", enqueued_by="agent:producer",
                        callback=True)
    handle = worker_handle(qm, tid)
    # Deferred, not finished: a live monitor is why a turn ending means
    # nothing. That record is the only place a live worker's words reach
    # disk, and it writes them as `last_text`.
    sm.arm_monitor(handle)
    await sm.finish(handle, text="halfway through the migration")
    assert qm.status(tid)["status"] == "dispatched"

    # Restart one: no session id was ever reported, so there is nothing to
    # resume and the task parks.
    qm2, _ = make_queue_rig(tmp_path, max_attempts=1)
    await qm2.start()
    assert qm2.status(tid)["status"] == "recoverable"
    assert "halfway" in (qm2.status(tid)["result"] or "")

    # Restart two: the park record is now the newest one for this task.
    qm3, _ = make_queue_rig(tmp_path, max_attempts=1)
    await qm3.start()
    assert qm3.status(tid)["status"] == "recoverable"
    assert "halfway" in (qm3.status(tid)["result"] or "")


# --------------------------------------------------------------------------
# A restore seam that cannot cold-rebuild parks, it does not crash the boot
# (carry-over B)
# --------------------------------------------------------------------------


class _NarrowSM:
    """A session manager with no resume seam.

    `_SessionManagerAdapter` (the standalone TUI's) had this shape until
    it learned `resume_from`: no `_sync_spawn`, and a `spawn` that takes
    neither `resume_from` nor `host`. `recovery.restore`'s cold-rebuild
    branch passes all three, so calling it is a TypeError — raised at
    boot, out of `QueueManager.start()`.
    """

    _sessions: list = []

    def __init__(self):
        self.spawned: list = []

    def spawn(self, slug, *, opening_prompt=None, handle=None, origin=None):
        self.spawned.append(handle)
        raise AssertionError("the narrow seam cannot be called with resume_from")

    def get(self, handle):
        return None

    async def close(self, handle):
        return None


async def test_a_session_manager_without_the_resume_seam_parks(tmp_path):
    """A task that cannot be restored degrades to "the operator can resume
    it", never to "the daemon will not boot"."""
    from aegis.queue import InboxRouter, QueueManager
    from aegis.queue.schema import Queue as QueueSpec

    qm, sm = make_queue_rig(tmp_path, max_attempts=2)
    tid, _ = qm.enqueue("impl", "go", enqueued_by="agent:producer",
                        callback=True)
    handle = worker_handle(qm, tid)
    # A session id IS on record here, so replay takes the cold-rebuild
    # branch rather than the "nothing to resume" one.
    sm.emit_system_init(handle, session_id="sess-1")
    sm.arm_monitor(handle)
    await sm.finish(handle, text="mid-flight")

    narrow = _NarrowSM()
    inbox = InboxRouter(state_dir=tmp_path)
    qm2 = QueueManager(
        {"impl": QueueSpec(name="impl", agent_profile="claude-impl",
                           max_parallel=1, max_attempts=2)},
        narrow, inbox, state_dir=tmp_path,
    )
    await qm2.start()
    assert qm2.status(tid)["status"] == "recoverable"
    # The park reason pins WHICH branch ran. "could not rebuild after the
    # restart" is `restore` returning None; the other park reason ("never
    # reached a turn boundary") would mean the record carried no session id
    # and the cold branch was never reached at all, which would make this
    # test green while proving nothing.
    assert qm2._all[tid].error == "could not rebuild after the restart"
    assert qm2._all[tid].resumable is not None
    assert narrow.spawned == []


async def test_the_tui_session_adapter_can_cold_rebuild_a_worker(pane_app):
    """The capability half of the same story: parking is the safe
    degradation, but the standalone TUI should still be able to rebuild a
    worker whose pane the front end did not restore. `restore` reaches the
    adapter through `getattr(sm, "_sync_spawn", sm.spawn)`.

    Driven through the adapter rather than read off its signature. Accepting
    `resume_from` and doing nothing with it is exactly the shape this
    capability regressed into before, and a signature check stays green
    through it: the session is built by `_make_session(**factory_kwargs)`,
    so the only thing that proves the worker comes back on its own
    conversation is the factory receiving the id.
    """
    from aegis.tui.app import _SessionManagerAdapter

    async with pane_app() as (pane, pilot):
        app = pane.app
        seen: list[dict] = []
        built = app._make_session

        def recording(agent, mcp_url, handle, **kwargs):
            seen.append(kwargs)
            return built(agent, mcp_url, handle)

        app._make_session = recording
        _SessionManagerAdapter(app).spawn(
            "default", handle="w-cold", resume_from="sess-1"
        )
        await pilot.pause()

        assert seen, "the adapter never built a session"
        assert seen[-1].get("resume_from") == "sess-1"


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------


def _server(qm, sm):
    from aegis.mcp.server import build_server
    from tests.stub_roots import StubRoots

    class _Bridge(StubRoots):
        def __init__(self):
            self.queue_manager = qm
            self.inbox_router = qm._inbox

        def list_sessions(self):
            return []

        def list_agents(self):
            return []

    return build_server(_Bridge())


async def _call(server, name, **kwargs):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    result = await tool.run(kwargs)
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
        return sc["result"]
    return sc if sc is not None else result.content[0].text


async def test_aegis_task_resume_looks_the_task_up_by_its_full_id(parked_rig):
    """`_all` is keyed by the full task id, which is what the tool takes and
    what `/queues tasks` prints. `Origin.detail` carries a four-character
    label for the fleet card and is read by no lookup, so a tool that
    truncated its argument would never find the task it was handed."""
    qm, sm, tid, handle, _ = parked_rig
    out = await _call(_server(qm, sm), "aegis_task_resume", task_id=tid)
    assert out["ok"] is True
    assert out["worker_handle"] == handle
    assert qm.status(tid)["status"] == "dispatched"


async def test_aegis_task_retry_returns_the_new_task_id(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    out = await _call(_server(qm, sm), "aegis_task_retry", task_id=tid)
    assert out["ok"] is True
    assert out["task_id"] != tid


async def test_aegis_task_resume_on_an_unknown_task_returns_a_dict(parked_rig):
    """Not a traceback: an MCP tool that raises costs the calling agent a
    turn and tells it nothing about what to do next."""
    qm, sm, tid, handle, _ = parked_rig
    out = await _call(_server(qm, sm), "aegis_task_resume", task_id="nope")
    assert out["ok"] is False and "nope" in out["error"]


async def test_the_briefing_names_both_new_tools():
    """`aegis_meta` is where an agent learns the plane exists at all."""
    from aegis.mcp.server import BRIEFING

    assert "aegis_task_resume" in BRIEFING
    assert "aegis_task_retry" in BRIEFING


# --------------------------------------------------------------------------
# TUI slash commands
# --------------------------------------------------------------------------


def _ctx(qm):
    class _Bridge:
        queue_manager = qm

        def inline_schedule_names(self):
            return set()

    return CommandContext(bridge=_Bridge(), handle="me")


async def test_slash_queues_tasks_lists_a_parked_task_with_its_id(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    res = await dispatch("/queues tasks", _ctx(qm))
    assert res.ok
    assert tid in res.body and "recoverable" in res.body and handle in res.body


async def test_slash_queues_tasks_on_an_unknown_queue_errors(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    res = await dispatch("/queues tasks ghost", _ctx(qm))
    assert not res.ok


async def test_slash_resume_puts_the_worker_back(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    res = await dispatch(f"/resume {tid}", _ctx(qm))
    assert res.ok
    assert qm.status(tid)["status"] == "dispatched"


async def test_slash_resume_reports_a_refusal_as_an_error(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    await sm.close(handle)
    qm._all[tid] = replace(qm._all[tid], resumable=None)
    res = await dispatch(f"/resume {tid}", _ctx(qm))
    assert not res.ok
    assert "no longer live" in res.body


async def test_slash_resume_works_across_a_restart(parked_rig):
    """`/resume` is the TUI half of the same fallback: a parked session the
    restart did not bring back still resumes, from the record."""
    qm, sm, tid, handle, _ = parked_rig
    await sm.close(handle)
    res = await dispatch(f"/resume {tid}", _ctx(qm))
    assert res.ok
    assert qm.status(tid)["status"] == "dispatched"


# --------------------------------------------------------------------------
# The read-only CLI
# --------------------------------------------------------------------------


@pytest.fixture
def cli_state(tmp_path, monkeypatch):
    """A queue log on disk plus a cwd the CLI resolves its state dir from."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


async def test_cli_ls_reads_parked_tasks_off_the_log(cli_state):
    from typer.testing import CliRunner

    from aegis.cli_queue import app

    qm, sm = make_queue_rig(cli_state / ".aegis" / "state", max_attempts=1)
    tid, _ = qm.enqueue("impl", "fix the deadlock", enqueued_by="agent:p")
    handle = worker_handle(qm, tid)
    sm.emit_system_init(handle, session_id="s1")
    await sm.fail(handle, text="stuck")

    out = CliRunner().invoke(app, ["ls"])
    assert out.exit_code == 0, out.output
    assert tid[-8:] in out.output.replace("\n", "")
    assert "recoverable" in out.output


async def test_cli_show_prints_the_task_and_its_events(cli_state):
    from typer.testing import CliRunner

    from aegis.cli_queue import app

    qm, sm = make_queue_rig(cli_state / ".aegis" / "state", max_attempts=1)
    tid, _ = qm.enqueue("impl", "fix the deadlock", enqueued_by="agent:p")
    handle = worker_handle(qm, tid)
    sm.emit_system_init(handle, session_id="s1")
    await sm.fail(handle, text="stuck")

    out = CliRunner().invoke(app, ["show", tid])
    assert out.exit_code == 0, out.output
    assert "recoverable" in out.output
    assert "enqueued" in out.output


def test_cli_show_on_an_unknown_task_exits_nonzero(cli_state):
    from typer.testing import CliRunner

    from aegis.cli_queue import app

    res = CliRunner().invoke(app, ["show", "nope"])
    assert res.exit_code != 0


def test_the_cli_has_no_acting_subcommand():
    """The daemon socket is a view-attachment stream, not RPC, so a
    standalone CLI process cannot ask a live brain to rebuild anything. A
    `resume` here could only edit the log and lie about it."""
    import typer

    from aegis.cli_queue import app

    names = {c.name for c in typer.main.get_command(app).commands.values()}
    assert names == {"ls", "show"}


# --------------------------------------------------------------------------
# The flagship path: park -> restart -> resume -> finish
# --------------------------------------------------------------------------


async def test_a_worker_parked_through_replay_can_finish_after_a_resume(tmp_path):
    """The path the whole feature is for, and the one `parked_rig` cannot
    reach: the park happened during REPLAY, not in this process.

    A task parked in-process still carries the observers dispatch attached,
    so `resume_task` never needed to re-attach them and the in-process test
    passes either way. A task parked by replay has none — `_park` popped
    `_workers` and nothing put the queue's `on_event`/`on_state` back — so a
    resume without `_attach_observers` produces a worker that runs, says it
    is done, and is never heard: the task stays `dispatched` forever, the
    producer's callback never fires, and the only `max_parallel` slot is
    held for the life of the process.

    In standalone TUI mode this is the NORMAL path, not an edge case:
    `app.py` replays the queue before it resumes agent tabs, so every parked
    task rehydrates with no session of its own.
    """
    from tests.test_queue_replay import (
        DISPATCHED,
        ENQUEUED,
        TID,
        WORKER,
        _rig,
        _stalled,
        _worker_session,
    )

    qm, sm, _ = _rig(
        tmp_path,
        [ENQUEUED, DISPATCHED, _worker_session(tmp_path), _stalled(2)],
        max_attempts=2,
    )
    # The front end's `plan_resume` got there first, which is what leaves a
    # live session under the handle for the replay to park onto.
    sm.preload_session(WORKER, session_id="sess-1")
    await qm.start()
    assert qm.status(TID)["status"] == "recoverable"

    assert (await qm.resume_task(TID))["ok"] is True
    await sm.finish(WORKER, text="finished after the restart")

    assert qm.status(TID)["status"] == "completed", (
        "the worker ended its turn and the queue never saw it"
    )
    assert qm.status(TID)["result"] == "finished after the restart"

    # The consequence the status hides: a slot that is never given back.
    nxt, _ = qm.enqueue("impl", "next", enqueued_by="agent:producer")
    assert qm.status(nxt)["status"] == "dispatched"
