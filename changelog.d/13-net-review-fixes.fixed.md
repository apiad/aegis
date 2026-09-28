- **Six defects in the network block, found by an adversarial review of the
  whole branch.** A `/net` reading that started before an outage could overwrite
  the sample that saw it, so the sidebar went back to green on a dead link;
  `refresh` is now serialised. An exit IP that could not be re-confirmed was
  shown as current, so moving to a captive network displayed your previous
  network's address beside a green tick; it now carries its age. `interval: 0`
  was accepted and turned the poller into a hot loop issuing tens of thousands
  of handshakes a second — every cadence and budget must now be positive, and
  only `speed_interval` may be `0`, because that is its off switch. The probe
  loop was released in `action_quit` alone, leaking one per app that exited any
  other way. `speed_timeout` was an httpx per-operation timeout rather than the
  budget it promised, so a link that kept trickling was never cut off. And an
  unbracketed IPv6 anchor was silently mangled into a host that never connects,
  which read as a permanent `✗ no egress`; brackets are now required, because
  `2606:4700:4700::1111:443` is itself a valid address and the form is genuinely
  ambiguous.
- **A complete throughput sample is no longer discarded for being fast.** The
  floor meant to prevent a division by a hair also threw away any megabyte that
  arrived in under 10 ms — above roughly 800 Mbps — so the row vanished on
  exactly the links good enough for the answer to be good news.
