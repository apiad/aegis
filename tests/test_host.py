"""Host meters: CPU, RAM and disk from /proc and statvfs."""

from pathlib import Path

from aegis.host import HostSampler, host_from, mount_of, real_mounts

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


def test_a_change_in_the_network_reading_alone_publishes(tmp_path):
    p = proc(tmp_path)
    net = {"ok": True, "rtt_ms": 24}
    sent = []
    h = HostSampler(
        lambda channel, ops: sent.append((channel, ops)),
        lambda channel: 1,
        tmp_path,
        proc=p,
        net=lambda: dict(net),
    )
    h.check()
    assert sent[-1][1][0]["set"]["net"] == {"ok": True, "rtt_ms": 24}
    h.check()  # the CPU share moves from since-boot to since-last-sample
    n = len(sent)
    h.check()
    assert len(sent) == n
    net["ok"] = False
    h.check()
    assert len(sent) == n + 1 and sent[-1][1][0]["set"]["net"]["ok"] is False


MOUNTS = """\
/dev/sda1 / ext4 rw,relatime 0 0
proc /proc proc rw,nosuid 0 0
tmpfs /run tmpfs rw,nosuid 0 0
overlay /var/lib/docker/overlay2/x/merged overlay rw 0 0
/dev/loop0 /snap/core22/2955 squashfs ro,nodev 0 0
/dev/sda15 /boot/efi vfat rw,relatime 0 0
/dev/sdb /mnt/data ext4 rw,relatime,discard 0 0
/dev/sdb /srv/bound ext4 rw,relatime 0 0
/dev/sdc1 /mnt/my\\040disk ext4 rw 0 0
"""


def test_the_mount_table_yields_only_the_disks():
    assert real_mounts(MOUNTS) == ["/", "/mnt/data", "/mnt/my disk"]


def test_two_disks_and_nothing_else():
    table = (
        "/dev/loop3 /snap/x/1 squashfs ro 0 0\ntmpfs /tmp tmpfs rw 0 0\n"
        "/dev/sda1 / ext4 rw 0 0\n/dev/sdb /mnt/data ext4 rw 0 0\n"
    )
    assert real_mounts(table) == ["/", "/mnt/data"]


def test_every_disk_is_listed_and_the_gauge_is_the_fullest(tmp_path):
    p = proc(tmp_path)
    (p / "self").mkdir()
    (p / "self" / "mounts").write_text(MOUNTS)
    h, _ = sampler(tmp_path, p)
    snap = h.sample()
    mounts = [d["mount"] for d in snap["disks"]]
    assert mount_of(tmp_path) in mounts and "/proc" not in mounts
    assert snap["disk"] == max(snap["disks"], key=lambda d: d["pct"])


def test_the_host_block_hides_and_adds_mounts(tmp_path):
    p = proc(tmp_path)
    (p / "self").mkdir()
    (p / "self" / "mounts").write_text("")
    here = mount_of(tmp_path)
    h = HostSampler(
        lambda c, o: None,
        lambda c: 1,
        tmp_path,
        proc=p,
        mounts=lambda: {"hide_mounts": [here], "add_mounts": []},
    )
    assert h.sample()["disks"] == [] and h.sample()["disk"] is None
    conf, err = host_from({"add_mounts": ["/proc/"]})
    assert err is None and conf["add_mounts"] == ["/proc"]
    assert host_from({"add_mount": []})[1] == (
        "host has no key 'add_mount'; it takes hide_mounts, add_mounts"
    )
    assert host_from({"hide_mounts": ["data"]})[1]
