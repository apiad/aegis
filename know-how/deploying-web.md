---
when: standing up, redeploying or debugging aegis on the VPS (dev.apiad.net) — the aegis-server + aegis-web unit pair behind Caddy
---

# Deploying aegis on the VPS (dev.apiad.net)

> **Not deployed yet, as of 2026-09-27.** Everything below describes the
> topology stage 6 *installs*. The running host is still the previous one: a
> single `aegis-web.service` running `aegis serve`, with Caddy `basicauth` in
> front. So on today's host: `systemctl restart aegis-server` fails because
> that unit does not exist; the redeploy block below is the procedure for
> cutting over, not for restarting what is there; there are no
> `/etc/caddy/Caddyfile.pre-*` backups from stage 6 yet; and the 401 check
> below passes for the wrong reason, because `basicauth` returns 401 too.
> Cut over with *Installing the unit pair* at the end of this doc, then
> delete this note.

aegis runs as two persistent services on the main VPS (`vps.apiad.net`,
`95.217.238.34`), rooted at `~/Workspace`, exposed at **https://dev.apiad.net**
behind Caddy. It serves the **opus / `permission: full`** default agent, so
whoever holds the token has full code execution and file access on the VPS
Workspace. The token is the only thing in front of that — keep it private.

## Topology

```
browser ──HTTPS──► Caddy (vps.apiad.net) ──► reverse_proxy 127.0.0.1:8899
        dev.apiad.net                                    │
                                                  aegis-web.service
                                                  (token + cookie, xterm.js)
                                                         │ unix socket
                                                  aegis-server.service
                                                  WorkingDirectory ~/Workspace
```

Two units, not one. The daemon holds the brain and binds **no TCP port at
all**: `aegis web` is an ordinary client of its unix socket, the same way
`aegis attach` is. Unit templates are in `scripts/`.

- **DNS**: `dev.apiad.net A 95.217.238.34`, managed via
  `HCLOUD_PROJECT=personal bin/dns` (apiad.net is a Hetzner-DNS zone in the
  *personal* project). `code.apiad.net` is a Hashnode blog — do not touch it.
- **Caddy**: site block in `/etc/caddy/Caddyfile` (Caddy **2.6.2**).
  `reverse_proxy 127.0.0.1:8899`; Caddy proxies the `/term` WebSocket upgrade
  transparently. No `basicauth`: the token below is the auth layer, and a
  second one only bought a second prompt. Backups at
  `/etc/caddy/Caddyfile.pre-*`.
- **`aegis-server.service`**: the daemon. `AEGIS_IDLE_TIMEOUT=0`, because it
  reaps itself after 30 idle minutes otherwise — right for a laptop, wrong for
  a unit meant to stay up.
- **`aegis-web.service`**: the web process, with two choices that matter.
  `--no-autostart`, so it never spawns a daemon of its own. Not because two
  daemons could hold the root — `DaemonLock` prevents that — but because
  `ensure_daemon` spawns `aegis server --autostarted`, and that mark is what
  lets a client stop it, so a browser's Ctrl+Q could take down the host's
  daemon. Such a daemon would also inherit this unit's environment rather than
  `aegis-server.service`'s, so no `AEGIS_IDLE_TIMEOUT=0` and no
  `Restart=always`: it would reap itself after 30 idle minutes and stay down.
  And `Wants=aegis-server.service`, **not `Requires=`** — with `Requires`,
  `systemctl restart aegis-server` restarts the web process too and drops every
  browser, the opposite of the reconnect the relay provides. The cost of
  `Wants=` is that a daemon which never starts leaves this unit serving tabs
  that draw nothing, so the relay logs that first failure at warning.
- **Config**: `~/Workspace/.aegis.yaml` carries a token-less
  `web: {bind: 127.0.0.1, port: 8899}` block; the token resolves from
  `AEGIS_WEB_TOKEN` (env wins over YAML — `config/yaml_loader.py::_build_web`).

## Secrets

One secret, not two. The token lives in `/etc/aegis-web.env`
(`AEGIS_WEB_TOKEN=…`, root-only) and reaches the unit via `EnvironmentFile`,
so it stays out of git and out of the unit file. A copy is at
`~/.aegis-web-token`.

Login is a **one-time** `https://dev.apiad.net/?t=<token>`, which 303s to `/`
and sets a session cookie (its name is the `COOKIE` constant in
`src/aegis/webterm/auth.py`). The token does not stay in the URL, so the
address bar is safe to screenshot and the query string does not end up in
history or in a referrer.

## Redeploy after code changes

Push to `main` first — the VPS clones from GitHub, so an unpushed commit
deploys nothing and reports success.

```bash
ssh vps 'cd ~/Workspace/repos/aegis && git pull --ff-only origin main'
ssh vps 'sudo systemctl restart aegis-server aegis-web'
```

Verify as a user reaches it, not just that the port answers:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://dev.apiad.net/          # 401 — no cookie
curl -s -o /dev/null -w '%{http_code}\n' https://dev.apiad.net/healthz   # 200
```

A **200** for the first one means the door is open: restore the Caddyfile
backup and reload Caddy immediately.

## The tombstone service worker

The retired PWA registered a service worker at scope `/` on this origin, and it
is still registered in every browser that ever loaded the old page. Deleting
the old client does **not** unregister it — a browser only drops a worker whose
script it cannot fetch, and only on an update check. So `/service-worker.js`
now serves a worker whose whole job is to unregister itself and drop its
caches. It is served without a cookie check, because an old worker's update
check carries no cookie of ours and a 401 would leave it installed for ever.

If a phone that once installed the PWA shows a stale shell, that worker has not
run its update check yet. Loading the page is what triggers it.

The new page has no manifest and no worker of its own; it is not installable.
That is a deliberate follow-up, not an oversight.

## Rotate the token

```bash
ssh vps 'openssl rand -hex 24 > ~/.aegis-web-token \
  && echo "AEGIS_WEB_TOKEN=$(cat ~/.aegis-web-token)" | sudo tee /etc/aegis-web.env \
  && sudo systemctl restart aegis-web'
```

Only `aegis-web` needs the restart: the daemon never reads the token.

## Installing the unit pair (the one-time cutover)

Push to `main` first. Then, in order — the daemon before the web process, so
the first browser finds a socket:

```bash
ssh vps 'cd ~/Workspace/repos/aegis && git pull --ff-only origin main && git log --oneline -1'
ssh vps 'sudo systemctl stop aegis-web && sudo systemctl disable aegis-web'
ssh vps 'sudo cp ~/Workspace/repos/aegis/scripts/aegis-server.service /etc/systemd/system/aegis-server.service'
ssh vps 'sudo cp ~/Workspace/repos/aegis/scripts/aegis-web.service /etc/systemd/system/aegis-web.service'
ssh vps 'sudo sed -i "s|%h|/home/apiad|g" /etc/systemd/system/aegis-server.service /etc/systemd/system/aegis-web.service'
ssh vps 'sudo systemctl daemon-reload && sudo systemctl enable --now aegis-server'
ssh vps 'sleep 5 && sudo systemctl enable --now aegis-web'
ssh vps 'systemctl is-active aegis-server aegis-web'
```

`%h` does not expand in a system unit, which is why the `sed` is there.

Then confirm the daemon holds no TCP listener and `aegis web` owns 8899:

```bash
ssh vps 'sudo ss -lntp | grep 8899'
ssh vps 'systemctl show -p MainPID aegis-web aegis-server'
```

Only then remove `basicauth` from the `dev.apiad.net` site block, leaving
`reverse_proxy 127.0.0.1:8899`. Back up first, and validate before reloading —
Caddy also fronts headscale on `vps.apiad.net`, so a bad config takes that down
too:

```bash
ssh vps 'sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-stage6'
ssh vps 'sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile'
ssh vps 'sudo systemctl reload caddy && systemctl is-active caddy'
```

**The 401 check only means something after this point.** Before basic auth is
removed, a 401 is Caddy's and tells you nothing about the token. After, it is
the token's. Verify in that order, and confirm a restart of the daemon does not
take the web process with it:

```bash
ssh vps 'sudo systemctl restart aegis-server'
ssh vps 'systemctl is-active aegis-web'    # still active — this is what Wants= buys
```

## Debug

- `systemctl status aegis-server aegis-web` +
  `journalctl -u aegis-server -u aegis-web -n 50`.
- Local, bypassing Caddy: `curl http://127.0.0.1:8899/healthz` on the VPS.
- **Which process owns 8899?** It must be `aegis-web`, never `aegis-server`:
  `sudo ss -lntp | grep 8899`, then check the PID against
  `systemctl show -p MainPID aegis-web`.
- A browser that connects but draws nothing usually means the daemon is down
  while the web process is up. The relay retries with backoff rather than
  refusing, so the tab recovers on its own once `aegis-server` is back; the
  reason is in `journalctl -u aegis-web`.
- Caddy: `journalctl -u caddy -n 50`; always `sudo caddy validate --config
  /etc/caddy/Caddyfile --adapter caddyfile` before `systemctl reload caddy`
  (Caddy also fronts headscale on `vps.apiad.net` — a bad config takes that
  down too).
