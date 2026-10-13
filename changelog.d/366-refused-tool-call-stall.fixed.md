- **A refused tool call no longer stalls the server.** fastmcp logged each
  refused aegis tool call (a `no_session`, a `quota_closed`) as a rich
  traceback, and rendering it held the event loop, and every session on it,
  for about half a second. The traceback is still logged, as plain text.
