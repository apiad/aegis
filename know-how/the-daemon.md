---
when: running, debugging or reasoning about the local daemon, such as why aegis did not pick up an edit, why a detach did not kill agents, what ls and kill do, or why a tab did not come back
---

# The daemon: `aegis` is a client

`aegis` boots no brain. A detached `aegis serve` holds the brain and every
view; your terminal connects to a unix socket under the project root and
pipes bytes both ways. Sessions outlive the terminal, several terminals can
watch one brain, and `Ctrl+Q` detaches rather than shutting anything down.

```
aegis                    attach this terminal (starting a daemon if none)
aegis attach --view ID   attach under an explicit view id
aegis ls                 daemons across every project root
aegis kill [--all]       stop this root's daemon, or all of them
aegis serve --cwd DIR    run the daemon in this terminal, where you can read it
```

## Where things live

The socket is `<root>/.aegis/state/daemon.sock`. Liveness is "the socket
accepts a connection", not "the file exists", because a SIGKILLed daemon
leaves the file behind.

Daemons across roots are recorded in `~/.aegis/daemons/*.json`, which is
what `ls` and `kill` read. `AEGIS_DAEMON_DIR` overrides that directory.

Each client gets a view, keyed per tty, and its focus, scroll and drafts
persist to `<root>/.aegis/state/views/<view-id>.json` when it detaches.
Which tabs *exist* is brain state and is not per view.

## A daemon keeps the code it booted with

This is the first thing to suspect when a fix does not appear. The daemon
is a running process: editing the source changes nothing until it
restarts, and quitting the TUI only detaches a view.

`aegis` checks for it now. If the newest source file is newer than the
daemon's start time, and the workspace snapshot shows nothing open, the
daemon is replaced silently. If anything is open, it prints a warning
naming the pid and attaches anyway, because refusing would lock you out of
a working brain. Pick the new code up with `aegis kill` when you can
afford to drop the tabs.

Comparing the recorded `version` would not catch this: a dev edit does not
bump a release number.

## The idle reaper

A daemon reaps itself after 1800s of **both** zero views and zero
sessions. Both conditions, deliberately: zero views alone would reap the
VPS daemon every night, which runs agents nobody is watching, and zero
sessions alone would never fire on a laptop with a stale tab open.
Idleness is a contiguous run, so any attach resets it. `AEGIS_IDLE_TIMEOUT`
overrides the duration in seconds; `0` disables reaping.

A daemon holding one tab therefore never exits on its own.

## Which tabs come back

Restoring a tab means resuming its harness conversation, and that needs
the provider's session id. A session only has one once its harness emitted
`SystemInit`, which happens on the first turn. So a tab you opened and
never used does not come back: there is no conversation to resume, and
`plan_resume` skips it with `no-session-id`.

Other reasons a tab is skipped, all reported the same way: `profile-missing`
when the agent named in the snapshot is no longer in `.aegis.yaml`, and
`driver-no-resume` when its provider cannot resume at all.

## When it will not start

The daemon's stderr goes to `/dev/null`, so it dies silently. Run it where
you can read it:

```
aegis serve --cwd <root>
```

The client parses the config before spawning, so a broken `.aegis.yaml` is
reported by `aegis` itself rather than as a spawn timeout. A damaged
`workspace.json` is not fatal: it is moved to `workspace.json.corrupt<N>`
and the TUI says where it went.

Crashes the daemon recorded on its way down are in
`<root>/.aegis/state/aegis.log`, and `aegis logs -c` prints only those.

## Known noise

`view client refused: client closed before hello` in the log is
`ensure_daemon` probing the socket for liveness and closing. It appears
once per invocation and means nothing.
