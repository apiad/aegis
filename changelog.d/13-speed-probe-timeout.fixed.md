- **The throughput probe no longer inherits the handshake's three-second
  timeout.** `network.timeout` governs a TCP handshake, where three seconds
  means a dead anchor. The 1 MB download was given the same budget, so it
  reported `unmeasured — ConnectTimeout (0 of 1000000 bytes in 3.16s)` and
  failed on precisely the slow links the reading exists to judge, while
  succeeding on the fast ones where the answer does not change any decision.
  It now has `network.speed_timeout`, default 30 seconds.
