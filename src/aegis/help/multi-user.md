---
title: Several people on one server
kind: howto
when: setting up several people on one server, each as their own Linux user, so members cannot see each other's sessions or files
hints:
  - Share one server with several people, each as their own Linux user: type /multiuser.
  - Each member sees only their own sessions, queues and files. #what-the-mode-does
---

This page takes a server from one person to several, each signed in with
their own token and each running as their own Linux user. It is written for
the agent that does the work with the person, which `/multiuser` starts:
numbered steps, the exact commands, and what each should print. Most steps
need root, which aegis never takes for itself.

## What the mode does

With `multi_user:` in `.aegis.yaml`, every token names a Linux user, and
everything a person starts runs as that user with no capabilities left: their
harness, terminals, monitors, workflow steps, queue workers and the git and
gh calls aegis makes for them. Files they open, send or attach are read and
written as them. So the kernel checks every file a person or their agent
touches, and their commits and pull requests are their own.

A member and their agents see only what the member owns: sessions, the
archive, queues, schedules, workflow runs, the journal and toasts. Someone
else's session answers as one that never existed. An admin sees everything.

## What it does not protect against

- An admin, and anyone with root on the host, can read everything.
- People share what the host shares: the quota gauges, the host's notices,
  the saved workflows, and whatever files the Linux permissions open to them.
  A world-readable file in someone's home is readable by everyone.
- Credentials put in the pool file (step 6) reach every person's harness, so
  each person's agent can read them and spend that account. A personal
  subscription may not be shared: Anthropic's consumer terms say "You may not
  share your Account login information, Anthropic API key, or Account
  credentials with anyone else". Use an account meant for a team, or have each
  person sign in to their harness with their own account, and check the terms
  of every other provider you pool.

## How to run this page

An agent doing this setup:

1. reads this page in full before it changes anything;
2. looks at the host first (step 1) and changes nothing while it looks;
3. tells the person in a few lines what the mode changes and what it does not
   protect against, from the two sections above;
4. asks who the people are, which folders they share, and where the harness
   credentials come from;
5. shows the plan as a list of commands and runs nothing until the person
   says yes. A command that needs root runs with sudo only when the agent's
   user has it and the person agrees; otherwise the agent hands it to the
   person to paste and waits for the output;
6. warns the person before the restart in step 8: this session stops and
   continues on its own after it;
7. ends at [Check](#check).

Sample names here are ana, ben and cara; `aegis` is the server's user only
where the page creates one.

## Before you start

The mode needs:

- Linux on x86_64 or aarch64;
- util-linux's setpriv: `setpriv --version` prints a version;
- systemd running aegis as a system unit, which step 8 installs;
- cgroup v2: `stat -fc %T /sys/fs/cgroup` prints `cgroup2fs`;
- each harness, and the tools agents use (git, gh, node), installed where
  every user can run them, such as `/usr/local/bin`. A harness installed in
  one person's home is not, since other users cannot enter that home. A
  switched process gets the login PATH from `/etc/login.defs`, not the
  server's.

## 1. Look at the host

Change nothing in this step. Run:

```bash
aegis doctor
systemctl status aegis --no-pager
id
getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 {print $1, $3, $6}'
getent group
aegis token list
```

Note the user aegis runs as, whether a unit runs it, the people who already
have Linux accounts, and the tokens. In the browser, see which sessions are
open now: the restart in step 8 continues them.

## 2. The server's user

The server runs as one unprivileged user, never root. On a host where aegis
already runs under a person's account, keep it: the server's own token stays
that person's admin token, its sessions keep running as that user, and
nothing moves. That person's home must not be readable by the others:
`stat -c %a ~` should print 700 or 750.

On a new host, create a service user and serve a directory it owns:

```bash
sudo useradd --system --create-home --home-dir /srv/aegis --shell /usr/sbin/nologin aegis
sudo chmod 700 /srv/aegis
```

To move an existing server to a new service user, stop it, then move its
`.aegis.yaml` and `.aegis/` into the new directory and give them to the new
user; the state directory, with its tokens and transcripts, comes along.
Sessions started before the move recorded no Linux user and run as the
server's user, which cannot enter the old account's home: archive them
before the move, or give their owner a token for that account.

## 3. A Linux user per person

For each person without an account:

```bash
sudo useradd --create-home --shell /bin/bash ben
sudo chmod 750 /home/ben
```

`useradd` without `--system` gives a uid at or above `UID_MIN` from
`/etc/login.defs`, which aegis requires. Home modes of 750 or 700 keep each
person's files from the others; `stat -c %a /home/*` checks them all.

## 4. Shared folders

For a folder some people share, make a group, give the folder to it with the
setgid bit, so new files take the folder's group, and add the people:

```bash
sudo groupadd team
sudo install -d -m 2770 -g team /srv/shared
sudo usermod -aG team ana
sudo usermod -aG team ben
```

`ls -ld /srv/shared` prints `drwxrws---` and the group `team`. A person's
processes pick up a new group when they start, so sessions started before
`usermod` do not have it until they restart.

## 5. A quota policy for each member

A member with no quota policy, and no default policy for members, can start
nothing. In Settings, set the default policy for members, or one per token
(see the page tokens). Admins are never held back.

## 6. The pool file

Skip this step when each person signs in to their harness with their own
account (step 10). Otherwise, put the variables every person's harness gets
in one file, readable by the server's user only:

```bash
sudo install -d -m 755 /etc/aegis
sudo install -m 600 -o aegis -g aegis /dev/null /etc/aegis/pool.env
sudoedit /etc/aegis/pool.env
```

Use the server's user in place of `aegis`. The file holds `NAME=value` lines;
blank lines and lines starting with `#` are skipped, and one pair of quotes
around a value is stripped. Claude Code reads `ANTHROPIC_API_KEY` for a
Claude Console key, or `CLAUDE_CODE_OAUTH_TOKEN` for a token from Claude
Code's setup-token command; either takes precedence over a person's own
sign-in. For another harness, its own documentation names the variable.
aegis reads the file again when it changes, for processes started after
that. Read the caveat in [What it does not protect
against](#what-it-does-not-protect-against) first.

## 7. Turn the mode on

Add to `.aegis.yaml`, in the directory the server serves:

```yaml
multi_user:
  env_file: /etc/aegis/pool.env
```

Leave out `env_file:` without a pool; `multi_user:` alone turns the mode on.
The aegis root, where `.aegis.yaml` lives, must not be writable by members.
The server reads the key at start only, and logs once that a restart is
needed; step 8 restarts it.

## 8. The unit and the restart

Tell the person now: the restart stops every session, this one included, and
each continues on its own when the server is back.

Write the unit with the capabilities the mode needs, from the served
directory:

```bash
aegis service install --multi-user --yes --user aegis
```

Use the server's user in place of `aegis`. It writes `./aegis.service`, whose
`[Service]` carries these lines, and prints the commands that install it:

```text
AmbientCapabilities=CAP_SETUID CAP_SETGID CAP_SETPCAP
CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_SETPCAP
```

Run them, then restart:

```bash
sudo install -m 644 aegis.service /etc/systemd/system/aegis.service
sudo systemctl daemon-reload
sudo systemctl enable aegis.service
sudo systemctl restart aegis.service
```

The server refuses to start in the mode without the capabilities, or as
root, with a line naming what is missing; `journalctl -u aegis -n 20`
shows it. At start it makes the state directory mode 0700, so no member can
read the tokens in it.

## 9. A token per person

```bash
aegis token add ben
```

It prints the token once; send it to the person by a private channel. The
token runs as the Linux user of the same name; `--linux-user` names another.
The user must exist, have a uid of at least `UID_MIN`, and be neither root
nor the server's own user. `--role admin` makes an admin, and
`--no-linux-user` an admin that runs as the server's user. `aegis token list`
shows each token's user.

## 10. Each person signs in to their tools

Each person signs in to aegis with their token in their own browser, opens a
terminal there, and runs, as themselves:

```bash
id -un
gh auth login
git config --global user.name "Ben Example"
git config --global user.email ben@example.com
```

`id -un` prints their own user. Without a pool file, they also sign in to
each harness here, Claude Code with its own login command, so its
credentials stay in their home.

## Add a person

To add a person to a server already in multi-user mode, run for that
person: step 3 (their Linux user), step 4 to add them to shared groups, step
5 if no default policy covers them, step 9 (their token) and step 10. No
restart is needed.

## Remove a person

1. Archive their sessions, or let them, since a session started later as a
   missing user is refused.
2. `aegis token revoke ben`: their browsers lose the sign-in on the next call.
3. Remove them from shared groups with `sudo gpasswd -d ben team`.
4. Only when their files are no longer needed: `sudo userdel -r ben`, which
   deletes their home.

## Turn the mode off

Remove `multi_user:` from `.aegis.yaml`, run `aegis service install` without
`--multi-user` and install the unit as in step 8, then restart. Every
session then runs as the server's user, which cannot enter members' homes:
a member's session that starts in their home fails until it is archived.

## Check

1. `aegis doctor` shows no error, and each multi_user row is ok:
   `multi_user.unit` (the unit grants the capabilities), `multi_user.tokens`
   (each member token has a Linux user), `multi_user.state` (mode 0700),
   `multi_user.env_file` with its count of variables when there is a pool,
   and `multi_user.harnesses` (every user can run each harness).
2. In a member's terminal, `id -un` prints the member's user and `echo $HOME`
   their home.
3. A member's Files tree lists their home and the shared folders, and opening
   another member's home fails with a permission error.
4. The person signs in with each member token in a second browser, or a
   private window, and sees only that member's sessions.
