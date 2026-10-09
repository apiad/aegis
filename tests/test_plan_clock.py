"""The plan clock: work and idle time accrued from record timestamps."""

from aegis.transcript.plan_clock import accrue, replan, switch


def items(*pairs, work=None):
    work = work or [0.0] * len(pairs)
    return [{"text": t, "state": s, "work_s": w} for (t, s), w in zip(pairs, work)]


def clock(work_s=0.0, idle_s=0.0, at=1000.0, running="work"):
    return {"work_s": work_s, "idle_s": idle_s, "at": at, "running": running}


def test_no_clock_is_no_time():
    p = items(("read", "doing"))
    assert accrue(p, None, 2000.0) == (p, None)
    assert switch(p, None, 2000.0, "idle") == (p, None)


def test_work_accrues_to_the_plan_and_the_doing_item():
    p, c = accrue(items(("read", "done"), ("fix", "doing")), clock(), 1060.0)
    assert [i["work_s"] for i in p] == [0.0, 60.0]
    assert c == clock(work_s=60.0, at=1060.0)


def test_idle_accrues_to_the_plan_only():
    p0 = items(("fix", "doing"))
    p, c = accrue(p0, clock(running="idle"), 1300.0)
    assert p == p0
    assert c == clock(idle_s=300.0, at=1300.0, running="idle")


def test_work_with_nothing_doing_accrues_to_the_plan_only():
    p0 = items(("read", "done"), ("fix", "pending"), work=[30.0, 0.0])
    p, c = accrue(p0, clock(work_s=30.0), 1010.0)
    assert [i["work_s"] for i in p] == [30.0, 0.0]
    assert c["work_s"] == 40.0


def test_a_timestamp_before_at_adds_nothing():
    p, c = accrue(items(("fix", "doing")), clock(at=1000.0), 990.0)
    assert c["work_s"] == 0.0 and p[0]["work_s"] == 0.0


def test_the_first_plan_starts_the_clock():
    p, c = replan([], None, [{"text": "read", "state": "doing"}], 1000.0, "work")
    assert p == items(("read", "doing"))
    assert c == clock(at=1000.0)


def test_a_new_plan_keeps_each_items_time_by_its_text():
    old = items(("read", "doing"), ("fix", "pending"))
    new = [
        {"text": "read", "state": "done"},
        {"text": "fix", "state": "doing"},
        {"text": "ship", "state": "pending"},
    ]
    p, c = replan(old, clock(), new, 1040.0, "work")
    assert [(i["text"], i["state"], i["work_s"]) for i in p] == [
        ("read", "done", 40.0),
        ("fix", "doing", 0.0),
        ("ship", "pending", 0.0),
    ]
    assert c == clock(work_s=40.0, at=1040.0)


def test_a_plan_sharing_no_text_starts_from_zero():
    old = items(("read", "done"), work=[500.0])
    p, c = replan(
        old,
        clock(work_s=500.0, idle_s=90.0),
        [{"text": "other", "state": "doing"}],
        1100.0,
        "work",
    )
    assert p == items(("other", "doing"))
    assert c == clock(at=1100.0)


def test_the_same_plan_again_changes_nothing():
    old, c0 = items(("read", "doing"), work=[5.0]), clock(work_s=5.0)
    p, c = replan(old, c0, [{"text": "read", "state": "doing"}], 1900.0, "work")
    assert p is old and c is c0


def test_switch_accrues_then_runs_the_new_class():
    p, c = switch(items(("fix", "doing")), clock(), 1020.0, "idle")
    assert p[0]["work_s"] == 20.0
    assert c == clock(work_s=20.0, at=1020.0, running="idle")


def test_switch_to_what_already_runs_changes_nothing():
    p0, c0 = items(("fix", "doing")), clock()
    p, c = switch(p0, c0, 1020.0, "work")
    assert p is p0 and c is c0
