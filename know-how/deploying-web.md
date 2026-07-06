# Deploying the aegis web UI (dev.apiad.net)

*When to reach for it: standing up / redeploying / debugging the public aegis
web client on the VPS.*

The aegis web PWA runs as a persistent service on the main VPS
(`vps.apiad.net`, `95.217.238.34`), rooted at `~/Workspace`, exposed at
**https://dev.apiad.net** behind Caddy (HTTPS + HTTP basic auth) plus the
aegis WS token. It serves the **opus / `permission: full`** default agent, so
whoever gets past both auth layers has full code execution + file access on the
VPS Workspace. Keep both secrets private.

## Topology

```
browser ──HTTPS──► Caddy (vps.apiad.net) ──basic_auth──► reverse_proxy
        dev.apiad.net                                    127.0.0.1:8899
                                                              │
                                                     aegis serve (systemd)
                                                     WorkingDirectory ~/Workspace
                                                     AEGIS_WEB_TOKEN (env)
```

- **DNS**: `dev.apiad.net A 95.217.238.34`, managed via
  `HCLOUD_PROJECT=personal bin/dns` (apiad.net is a Hetzner-DNS zone in the
  *personal* project). `code.apiad.net` is a Hashnode blog — do not touch it.
- **Caddy**: site block in `/etc/caddy/Caddyfile` (Caddy **2.6.2** → the
  directive is `basicauth`, NOT `basic_auth`). `reverse_proxy 127.0.0.1:8899`;
  Caddy proxies the `/ws` WebSocket upgrade transparently. Backups at
  `/etc/caddy/Caddyfile.pre-aegis.*`.
- **systemd**: `aegis-web.service` — `User=apiad`,
  `WorkingDirectory=/home/apiad/Workspace`, `Restart=always`,
  `ExecStart=/usr/local/bin/uv run --project ~/Workspace/repos/aegis aegis serve`.
  The token is in `/etc/aegis-web.env` (`AEGIS_WEB_TOKEN=…`, root-only) via
  `EnvironmentFile` — kept out of git and out of the unit.
- **Config**: `~/Workspace/.aegis.yaml` carries a token-less
  `web: {bind: 127.0.0.1, port: 8899}` block; the token resolves from
  `AEGIS_WEB_TOKEN` (env wins over YAML — `config/yaml_loader.py::_build_web`).

## Secrets

- aegis WS token: `~/.aegis-web-token` on the VPS (also in `/etc/aegis-web.env`).
- basic-auth password: `~/.aegis-web-basicpw` on the VPS. Username: `apiad`.
- Login URL: `https://dev.apiad.net/?t=<token>` (browser prompts for basic auth
  first; the token rides in the query string).

## Redeploy after code changes

Push to `main` first (the VPS clones from GitHub — unpushed = no-op). Then:

```bash
ssh vps 'cd ~/Workspace/repos/aegis && git pull --ff-only origin main \
  && sudo systemctl restart aegis-web && systemctl is-active aegis-web'
```

Verify: `curl -u apiad:<pw> https://dev.apiad.net/healthz` → `{"ok":true}`.

**Known gap:** the service worker cache name is `aegis-shell-0` because
`aegis serve` doesn't pass its real version to `WebFrontend` (defaults to
`"0"`). So a redeploy does NOT auto-bust the installed PWA shell cache — clients
keep the old JS until they clear the SW. Fix = thread `aegis.__version__` into
`WebFrontend(server_version=…)`. Until then, hard-refresh / update the SW
manually after a redeploy that changes client JS.

## Rotate secrets

```bash
ssh vps 'openssl rand -hex 24 > ~/.aegis-web-token \
  && echo "AEGIS_WEB_TOKEN=$(cat ~/.aegis-web-token)" | sudo tee /etc/aegis-web.env \
  && sudo systemctl restart aegis-web'
# basic-auth: regenerate ~/.aegis-web-basicpw, caddy hash-password, edit the
# dev.apiad.net block in /etc/caddy/Caddyfile, caddy validate, systemctl reload caddy.
```

## Debug

- `systemctl status aegis-web` + `journalctl -u aegis-web -n 50`.
- Local (bypass Caddy): `curl http://127.0.0.1:8899/healthz` on the VPS.
- Caddy: `journalctl -u caddy -n 50`; always `sudo caddy validate --config
  /etc/caddy/Caddyfile --adapter caddyfile` before `systemctl reload caddy`
  (Caddy also fronts headscale on `vps.apiad.net` — a bad config takes that
  down too).
