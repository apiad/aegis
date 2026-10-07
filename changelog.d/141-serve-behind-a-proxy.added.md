- **`aegis serve --origin` runs aegis behind an HTTPS reverse proxy.** The
  websocket accepted only a loopback `Host` with an `http://` origin, so behind
  Caddy at a public name every socket closed with 4403 and the page never booted.
  `--origin https://dev.example` (repeatable) accepts sockets whose `Host` is that
  origin's host and whose `Origin` is exactly that origin; the token is still
  required, and `serve` prints the public URL next to the local one.
