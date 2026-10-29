---
title: Running aegis under systemd
kind: howto
when: running aegis as a systemd service that survives reboots, sizing its memory caps, or checking an installed unit
hints:
  - Run aegis as a systemd service so it survives reboots. #write-the-unit
---

On a host that must survive reboots, run `aegis serve` under systemd.
`aegis service install` writes the unit for you and never runs `sudo`: it
prints the commands that need it, for you to read and run.

## Write the unit

Run it from the directory aegis should serve:

```bash
aegis service install
```

It asks for the user the server runs as, the directory it serves, the port,
the aegis executable, and the two memory caps. Each question shows its
default: you, the current directory, 8742, the `aegis` on your PATH, and
`MemoryHigh` and `MemoryMax` at about 70% and 80% of the host's RAM from
`/proc/meminfo` (5.5G and 6.5G on a host with 8 GB). The unit carries your
PATH, so the server finds `claude` and the other harnesses where you do.

It writes `./aegis.service` and prints the three commands that install and
start it:

```bash
sudo install -m 644 /home/you/work/aegis.service /etc/systemd/system/aegis.service
sudo systemctl daemon-reload
sudo systemctl enable --now aegis.service
```

Every question is also a flag: `--user`, `--root`, `--port`, `--aegis`,
`--memory-high` and `--memory-max`. `--yes` takes the default for any you do
not give and asks nothing, so an agent can run it. `--output` writes the unit
somewhere else, and its file name becomes the unit's name. `--dry-run` prints
the unit and writes nothing.

## What the unit sets

Every session, and everything its agent starts, lives in the server's unit,
so the unit decides what one runaway session can take down. The unit's
comments say why each line is there:

- `KillMode=mixed`: systemd stops the server and the server ends its agents.
- `Restart=always` and `RestartSec=3`; a restart continues the turns it cut.
- `OOMPolicy=continue`: one child killed for memory does not stop the unit.
- `MemoryHigh` throttles the unit before the host runs out, and `MemoryMax`
  caps it below the host's RAM, so a runaway meets an OOM inside the unit.
- `OOMScoreAdjust=-500`: in a host-wide OOM the kernel picks the server last,
  and the agents' processes, raised to 500, first.
- `Delegate=yes`: aegis gives each session a cgroup of its own, capped by
  `session_memory_max:` in `.aegis.yaml`, so a runaway session is killed alone,
  and caps all the work it starts together, below the unit's limits, with
  memory, tasks, CPU and disk priority kept for the server (config#memory-per-session).

Do not add `ManagedOOMMemoryPressure=kill`: systemd-oomd would choose among
every cgroup in the unit, the server's included, or, without delegation, kill
the whole unit.

## Check an installed unit

`aegis doctor` looks for a unit under `/etc/systemd/system/` or
`~/.config/systemd/user/` whose `ExecStart` runs `aegis serve`, reads it with
its drop-ins through `systemctl cat`, and warns once for each of the settings
above that differs from what `aegis service install` would write on this
host, with the value it found and the one recommended:

```text
warn  service.aegis.service.KillMode KillMode is control-group; aegis service install sets mixed
```

A setting the unit leaves out shows as `unset`. Sizes compare by value, so
`MemoryHigh=5905580032` matches `5.5G`. With no aegis unit installed, the
doctor says nothing about one.
