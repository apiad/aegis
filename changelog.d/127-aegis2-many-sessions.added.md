- **aegis2 runs many sessions, survives restarts, and keeps an archive.** The tab
  bar is the server's open sessions, the same in every browser, in an order each
  browser keeps by drag. Fleet is the home view, with a card per session and the
  archive below it. A server restart brings every session back stopped, and the
  next prompt resumes it with `claude --resume`; Stop ends a process and keeps
  the tab, Close moves a session to the archive for every browser, Reopen brings
  it back, and handles and titles rename in place. Still experimental.
