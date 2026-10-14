---
title: Terminals
kind: howto
when: you want a shell or a long-running command on the server in a browser tab, or a dev server that comes back after a restart of aegis
---

A terminal is a shell or a command that runs on the server, held by aegis. It
keeps running when you close the browser, and a browser that opens it again
sees what it printed meanwhile.

## Opening one

Press + and pick one under Terminals. `shell` is always there: your login shell
in the config root. The others come from the `terminals:` block of
`.aegis.yaml`:

```yaml
terminals:
  api:  {cmd: "uvicorn app.main:app --reload", cwd: app, restart: on-failure,
         env: {PORT: "8000"}}
  logs: {cmd: "journalctl -fu nginx", cwd: /}
```

`cmd` and `cwd` are required; a relative `cwd` is under the config root.
`restart` is `no` unless you set `on-failure` or `always`, which makes the
profile a service: a terminal opened from it starts again when aegis does. `env` adds to the
server's environment, and every terminal also gets `TERM=xterm-256color` and
`AEGIS_TERMINAL` set to its name. `aegis doctor` checks each profile.

`terminal_memory_max:` caps the memory of each terminal's processes, as
`session_memory_max:` does for sessions, where the server's unit delegates its
cgroup.

## Typing

Click the terminal to type into it directly: every key goes to the program,
including arrows, Tab and Ctrl-C. Esc, or a click outside, leaves; a program
that needs Esc gets it from Ctrl-[ or, on a phone, from the row of keys under
the terminal. The box under the terminal sends a whole line when you press
Enter, and Up and Down in an empty box bring back the lines you sent.

A terminal has one size. The browser you type in sets it to fit your window;
other browsers draw it at that size, with a smaller font if they must.

## Closing, restarting, the archive

Close ends the program with SIGHUP, then SIGKILL five seconds later, and moves
the terminal to the archive, where Read replays what it printed. A terminal
whose program exited stays in its tab, marked grey, or red for a non-zero exit
code, with a Restart button that runs the same profile again in the same tab.

A restart of aegis ends every terminal's program. Services start again; other
terminals show `ended: server_stopped` with their output and a Restart button.

## Who may type

Every person on the server can read every terminal. A member types into,
resizes, restarts and closes only their own; an admin, any. Agents cannot use
terminals yet.

## Where the output is

Each terminal's output is in `.aegis/state/terminals/<id>/cast.jsonl`, an
asciicast v2 recording that `asciinema play` plays, kept to 16 MB in two
segments. What you type is not stored; what the program echoes is.