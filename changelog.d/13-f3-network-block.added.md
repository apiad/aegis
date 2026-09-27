- **F3's SYSTEM block now answers whether the network works.** A `NET` row
  carries egress liveness with its handshake RTT, the Cloudflare colo, and an
  opt-in throughput figure; the row under it carries the exit IP. The two
  questions it exists for are the ones that decide what an agent should do
  next — on a captive network egress dies silently and the failures arrive
  later looking like bugs in whatever was being built, and a link measured at
  139 KB/s is forty times under the threshold where work stays local instead
  of going to a server.

  Three readings on three cadences, because they differ by a factor of three
  thousand in cost: a TCP handshake every 20s, a 300-byte lookup every 300s
  and whenever egress comes back, and a megabyte download only when
  `network.speed_interval` is set or you type `/net`. TCP rather than ICMP,
  because a captive portal answers `ping` and refuses the connection. A
  throughput figure is never shown without its age, and never at all when the
  transfer delivered less than it asked for.
