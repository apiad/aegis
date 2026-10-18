---
title: Commands
kind: concept
when: looking up an aegis slash command or an aegis command-line subcommand
---

aegis has two sets of commands: slash commands typed in a session's message
box, and subcommands of the `aegis` program in a shell. A slash command that
is not aegis's goes to the session's harness as typed, so Claude Code's own
commands, such as its /compact, still work. Where both have a command of the
same name, aegis's takes its place: aegis's /loop runs on the server and
survives a restart, where the harness's would end with its process.

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
| `/btw <question>` | ask a side question about this session, even while the agent works. aegis answers it from the transcript with the agent `btw:` names in `.aegis.yaml`, in one call with no tools, and shows the answer as a block you can fold. The agent never sees the question or the answer. One runs at a time per session; the block's cancel stops it |
| `/workflow [id] [text]` | run a saved workflow now: the agent makes its args from your text and starts it, with no card to approve. Alone, it opens the menu of saved workflows |
| `/loop <prompt>` | repeat a prompt in this session until the agent says it is done, at a fixed interval when the prompt gives one ("every 5m") or at the agent's pace; Stop on its card ends it |
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
| `aegis map` | a map of the git repository holding the current directory, or `--repo`, for an agent learning its code; see the code map page |
| `aegis map repo [path]` | its folders and files, with definition counts and each module's first doc line; `--depth` |
| `aegis map deps [path]` | which files use which, with how many uses |
| `aegis map outline <path>` | a file's or folder's classes and functions: line, signature, first doc line |
| `aegis map find <name>` | where a name is defined |
| `aegis map show <symbol>` | the source of one definition |
| `aegis map callers <symbol>` | every call of a definition, by file; `--depth` for callers of callers |
| `aegis map callees <symbol>` | what a definition calls |
| `aegis map impact <symbol>` | every definition that uses it, and what uses those; `--depth` (3) |
| `aegis call <tool> [args]` | from an agent's shell, call one of its aegis tools as that agent; see below |
| `aegis tools [tool]` | from an agent's shell, list its aegis tools, or print one tool's description and JSON schema |
| `aegis import-legacy <dir>` | copy the sessions of aegis before 2.0 into the archive, to read |

Every subcommand but `aegis map` takes `--root` to name the root instead of finding it from
the current directory (for `aegis init`, the directory to write into), and
`--help` for its full list of options.
`aegis --version` prints the version.

## Tools from an agent's shell

Every tool an agent has is also a shell command: `aegis call <tool>` with the
tool's arguments as `key=value` pairs, `aegis call <tool> '<json>'` with one
JSON object, or `aegis call <tool> -` to read that object from standard input.
A value is read as JSON when it parses, so `limit=3` is a number and
`paths='["a.py"]'` a list. The call acts as the session whose shell made it,
with the same permissions and the same result as the tool, and prints that
result; a tool's error goes to standard error with exit code 1.
`aegis tools` lists the tools, and `aegis tools <tool>` prints one tool's
description and JSON schema.

```bash
# Count the words of three help pages without reading them
for p in monitors queues handoffs; do
  echo "$p $(aegis call help_read page=$p | wc -w)"
done
```

Use it for loops over many calls and for work on what a tool returns:
counting, filtering, joining, piping into another command. The loop is one
Bash call, and the results never enter the agent's context. A single call is
as well made with the tool.

aegis gives each session's process `AEGIS_MCP_URL` and `AEGIS_SESSION_TOKEN`,
the same endpoint and token as its tools, and every command the agent runs
inherits them. A program the agent starts, such as a test run or a build
script, can therefore act as the agent. Outside a session neither is set, and
`aegis call` says so and exits with code 2. The token ends with the agent's
process: a restart mints a new one.

## Serving

`aegis serve` prints the address to open, with a token in it. Opening that
address once signs the browser in with a cookie that lasts a year, and the
token leaves the address bar. The admin's token is in `.aegis/state/token`
under the root. With `--detach` aegis serves in the background and keeps
running after the shell closes; its log is `.aegis/state/serve.log`, and it
prints the process id to stop it with.
