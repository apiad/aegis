from aegis.queue.digest import QueueView, Snapshot, TaskView
from aegis.tui.strip import render_strip
from aegis.tui.themes import aegis_colors, INK

PAL = aegis_colors(INK)


def _qv(name="tasks", agent="gemini-flash", parallel=2,
        running=0, queued=0, ok=0, err=0):
    return QueueView(
        name=name, agent=agent, max_parallel=parallel,
        running=running, queued=queued, ok=ok, err=err)


def _tv(handle="brisk-curie", state="running"):
    return TaskView(
        task_id="t1", queue="tasks", state=state,
        payload_summary="summarize TASKS.md",
        worker_handle=handle, agent_slug="gemini-flash",
        from_sender="agent:lucid",
        enqueued_at=None, dispatched_at=None,
        started_at=None, completed_at=None)


def test_strip_hidden_when_no_queues():
    snap = Snapshot(queues=[], tasks=[], last_started=None)
    assert render_strip(snap, PAL).plain == ""


def test_strip_single_queue_format():
    snap = Snapshot(
        queues=[_qv(running=1, queued=3, ok=14, err=2)],
        tasks=[],
        last_started=_tv())
    txt = render_strip(snap, PAL).plain
    assert "tasks" in txt
    assert "●1/2" in txt
    assert "○3" in txt
    assert "✓14" in txt and "✗2" in txt
    assert "last:" in txt and "brisk-curie" in txt


def test_strip_two_queues_compact_format():
    snap = Snapshot(
        queues=[
            _qv(name="tasks", running=1, queued=3),
            _qv(name="impl",  running=0, queued=0, parallel=1),
        ],
        tasks=[], last_started=None)
    txt = render_strip(snap, PAL).plain
    assert "tasks" in txt and "impl" in txt
    assert "●1/2" in txt and "●0/1" in txt


def test_strip_four_plus_queues_aggregate():
    snap = Snapshot(
        queues=[_qv(name=f"q{i}", parallel=2, running=(1 if i < 3 else 0))
                for i in range(5)],
        tasks=[], last_started=None)
    txt = render_strip(snap, PAL).plain
    assert "5 queues" in txt
    assert "●3/10" in txt


def test_strip_no_running_omits_last():
    snap = Snapshot(
        queues=[_qv(queued=2)], tasks=[], last_started=None)
    txt = render_strip(snap, PAL).plain
    assert "last:" not in txt
