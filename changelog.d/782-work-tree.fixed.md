- **Nothing aegis starts can starve the server any more.** Terminals and
  browsers now share the memory cap the sessions got in this release, so a
  build in a terminal or a heavy page counts against it too. Each session,
  terminal and browser may run at most 4096 tasks, and all of them together
  leave the server 512, so a fork bomb dies in its own session instead of
  leaving the server unable to start a thread. The server's disk requests
  now go first too. Chromium no longer runs in the server's own cgroup.
