---
title: Commands
kind: concept
when: looking up an aegis slash command or an aegis command-line subcommand
---

aegis has two sets of commands: slash commands typed in a session's message
box, and subcommands of the `aegis` program in a shell. A slash command that
is not aegis's goes to the session's harness as typed, so Claude Code's own
commands, such as its /compact, still work.

## Slash commands

Type / in a session's box to open the menu of every command, aegis's first;
Tab or Enter picks one. Alt+/ opens the same menu over a draft.

| command | what it does |
|---|---|
| `/model <name>` | switch this session's model |
| `/effort <level>` | switch its reasoning effort: low, medium, high, xhigh or max, as far as the model takes them |
| `/permission <mode>` | switch what the agent may do: read, write, auto or full |
| `/rename <handle>` | rename this session's handle |
| `/title <text>` | set this session's title |
| `/stop` | stop the process and keep the session |
| `/spawn <agent>[@server] [prompt]` | start a session, here or on a linked server; flags `--quiet` (no tab until its turn ends; needs a prompt), `--model`, `--effort`, `--permission` and `--cwd` go before the prompt |
| `/close` | close the session; it stays in the archive. The browser asks first unless the session is done |
| `/help` | open the menu listing every command |
| `/help <query>` | open the Help tab with those words searched, such as `/help queues` |
| `/tutorial [page]` | start a guide session in a new tab that walks you through a help page, one step per turn. With no page it runs `getting-started`; after the space, the menu lists the tutorial pages. Typed in a session, the guide runs on the agent `default_agent:` names, in that session's directory, and nothing is sent to the session you typed it in. The hint under the new tab's chips does the same with the agent and chips you picked there. |
| `/whats-new` | start a guide session in a new tab that walks you through what changed in aegis since your last tour, one change per turn, opening each change's help page to show you how to use it. It covers the releases after the last one you toured, up to the version you run, from the release notes that ship with it; if you have toured this version already, it tours it again. Typed in a session, the guide runs on the agent `default_agent:` names, in that session's directory. The hint under the new tab's chips does the same with the chips you picked there, and its × marks this version toured without starting a guide. |

A line that starts with // is sent as text, its first slash turned into a
space so the harness does not read a command, for when you mean the text. When
the harness has told aegis its commands, a slash line that names none of them
and no aegis command is refused before it costs a turn.

## The aegis program

| command | what it does |
|---|---|
| `aegis` | serve this directory's root and open a browser window on it |
| `aegis serve` | serve the root: `--port` (8742), `--host` (127.0.0.1), `--name` (this server's name, the host name by default), `--origin` for a public proxy's address, `--window` to open a window, `--detach` to run in the background |
| `aegis init` | write a first `.aegis.yaml` from the harnesses installed here; `--yes` takes every proposal |
| `aegis doctor` | check `.aegis.yaml`, the harnesses it names and the state directory, and warn where an installed aegis systemd unit differs from what `aegis service install` writes; exits 1 on an error |
| `aegis service install` | write a systemd unit for `aegis serve` with the recommended settings and print the `sudo` commands that install it; `--yes` takes every default, `--dry-run` prints the unit, `--output` names the file. See the systemd page |
| `aegis link add <name> <url>` | link another aegis server, reading its token from standard input; `--as` names this server there |
| `aegis link list` | the linked servers, by name and URL |
| `aegis link remove <name>` | end a link |
| `aegis token add <name>` | make a token for a person, a linked server or a job; `--role member` (the default) or `--role admin`. The token is printed once |
| `aegis token list` | the tokens, their roles and quota policies |
| `aegis token revoke <name>` | end a token; revoking the server's own admin token, the one in `.aegis/state/token`, prints its replacement |
| `aegis journal search [pattern]` | search what was done: `--since`, `--until`, `--path`, `--session`, `--kind`, `--limit` |
| `aegis journal rebuild` | rebuild the journal from every transcript |
| `aegis usage` | what the agents' work cost: `--by` month, dow or hour, `--sessions`, `--tools`, `--since` |
| `aegis usage repo <path>` | the cost of one git repository's work |
| `aegis usage repos <dir>` | the cost of every repository directly under a directory |
| `aegis import-legacy <dir>` | copy the sessions of aegis before 2.0 into the archive, to read |

Every subcommand takes `--root` to name the root instead of finding it from
the current directory (for `aegis init`, the directory to write into), and
`--help` for its full list of options.
`aegis --version` prints the version.

## Serving

`aegis serve` prints the address to open, with a token in it. Opening that
address once signs the browser in with a cookie that lasts a year, and the
token leaves the address bar. The admin's token is in `.aegis/state/token`
under the root. With `--detach` aegis serves in the background and keeps
running after the shell closes; its log is `.aegis/state/serve.log`, and it
prints the process id to stop it with.
