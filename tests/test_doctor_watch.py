"""DoctorWatch: one run at a time, again on a config change, and on a timer."""

import asyncio

from aegis.config import Finding
from aegis.doctor_watch import DoctorWatch

from .conftest import until


def counting():
    runs = []

    async def run():
        runs.append(1)
        await asyncio.sleep(0.01)
        return [Finding("warn", "x", f"run {len(runs)}"), Finding("ok", "y", "fine")]

    return runs, run


async def test_it_runs_first_after_the_delay_and_keeps_only_problems():
    runs, run = counting()
    w = DoctorWatch(run, first_after=0.01, every=60)
    w.start()
    await until(lambda: runs, timeout=2, what="the first run")
    await until(lambda: w.findings, timeout=2, what="its findings")
    assert [f.where for f in w.problems()] == ["x"]
    await w.stop()


async def test_a_config_change_runs_it_again():
    runs, run = counting()
    w = DoctorWatch(run, first_after=0, every=60)
    w.start()
    await until(lambda: len(runs) == 1, timeout=2, what="the first run")
    w.kick()
    await until(lambda: len(runs) == 2, timeout=2, what="the run after the kick")
    await w.stop()


async def test_a_config_change_cuts_the_first_wait_short():
    runs, run = counting()
    w = DoctorWatch(run, first_after=60, every=60)
    w.start()
    w.kick()
    await until(lambda: len(runs) == 1, timeout=2, what="the run after the kick")
    await w.stop()


async def test_the_timer_runs_it_again():
    runs, run = counting()
    w = DoctorWatch(run, first_after=0, every=0.05)
    w.start()
    await until(lambda: len(runs) >= 3, timeout=2, what="timed runs")
    await w.stop()


async def test_two_refreshes_never_overlap():
    live, most = [0], [0]

    async def run():
        live[0] += 1
        most[0] = max(most[0], live[0])
        await asyncio.sleep(0.02)
        live[0] -= 1
        return []

    w = DoctorWatch(run)
    await asyncio.gather(w.refresh(), w.refresh(), w.refresh())
    assert most[0] == 1


async def test_errors_come_before_warnings():
    async def run():
        return [Finding("warn", "a", "w"), Finding("error", "b", "e")]

    w = DoctorWatch(run)
    await w.refresh()
    assert [f.level for f in w.problems()] == ["error", "warn"]
