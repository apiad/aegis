"""Host meters: CPU, RAM and disk from /proc and statvfs."""

from pathlib import Path

from aegis.host import HostSampler

STAT = "cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 0 0 1 0 0 0 0 0 0\n"
MEMINFO = "MemTotal:       {total} kB\nMemFree:          1000 kB\nMemAvailable:   {avail} kB\n"


def proc(tmp_path: Path, *, busy=100, idle=900, total=32_000_000, avail=24_000_000):
    p = tmp_path / "proc"
    p.mkdir(exist_ok=True)
    (p / "stat").write_text(STAT.format(busy=busy, idle=idle))
    (p / "meminfo").write_text(MEMINFO.format(total=total, avail=avail))
    return p


def sampler(tmp_path, p, subscribers=1):
    sent = []
    h = HostSampler(
        lambda channel, ops: sent.append((channel, ops)),
        lambda channel: subscribers if channel == "host" else 0,
        tmp_path,
        proc=p,
    )
    return h, sent


def test_cpu_is_the_busy_share_between_two_samples(tmp_path):
    p = proc(tmp_path, busy=100, idle=900)
    h, _ = sampler(tmp_path, p)
    assert h.sample()["cpu"] == 10  # since boot, on the first sample
    proc(tmp_path, busy=160, idle=940)  # +60 busy, +40 idle
    assert h.sample()["cpu"] == 60


def test_ram_is_total_minus_available(tmp_path):
    h, _ = sampler(
        tmp_path, proc(tmp_path, total=32 * 1024 * 1024, avail=24 * 1024 * 1024)
    )
    assert h.sample()["ram"] == {"pct": 25, "used_gb": 8.0, "total_gb": 32.0}


def test_disk_reads_the_filesystem_of_the_given_path(tmp_path):
    h, _ = sampler(tmp_path, proc(tmp_path))
    disk = h.sample()["disk"]
    assert 0 <= disk["pct"] <= 100 and disk["total_gb"] > 0


def test_no_proc_means_no_snapshot(tmp_path):
    h, sent = sampler(tmp_path, tmp_path / "missing")
    assert h.snapshot() is None
    h.check()
    assert sent == []


def test_nothing_is_sampled_or_published_without_a_subscriber(tmp_path):
    p = proc(tmp_path)
    h, sent = sampler(tmp_path, p, subscribers=0)
    (p / "stat").unlink()  # a read would now fail loudly in the test
    h.check()
    assert sent == []


def test_a_change_in_a_whole_percent_publishes_and_nothing_else_does(tmp_path):
    p = proc(tmp_path)
    h, sent = sampler(tmp_path, p)
    h.check()
    assert len(sent) == 1 and sent[0][0] == "host"
    proc(tmp_path, busy=200, idle=1800)  # same 10% busy share
    h.check()
    assert len(sent) == 1
    proc(tmp_path, busy=200, idle=1800, avail=16_000_000)  # RAM moves
    h.check()
    assert len(sent) == 2
    assert sent[-1][1] == [{"set": h.snapshot()}]
