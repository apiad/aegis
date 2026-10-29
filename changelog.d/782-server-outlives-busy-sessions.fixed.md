- **Many busy sessions no longer stall the server.** Each session had its own
  memory cap, but together they could fill the service up to its
  `MemoryHigh`, and the kernel then throttled the server with them: aegis
  stayed up but stopped answering. The sessions are now capped together below
  the service's limit, the server keeps 512 MiB they cannot reclaim, and it
  runs before them when every CPU is busy.
