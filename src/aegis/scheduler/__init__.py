"""Scheduler substrate — cron-style scheduled workflow execution.

Sits inside `aegis serve` as a peer to QueueManager and InboxRouter.
A tick loop walks the loaded schedule table, dispatches eligible
entries to ``runner.run_workflow``, and logs lifecycle events to
``.aegis/state/schedules/<name>.jsonl``.
"""
from aegis.scheduler.clock import Clock, FakeClock, SystemClock
from aegis.scheduler.scheduler import Scheduler, SchedulerConfig

__all__ = [
    "Clock", "FakeClock", "Scheduler", "SchedulerConfig", "SystemClock",
]
