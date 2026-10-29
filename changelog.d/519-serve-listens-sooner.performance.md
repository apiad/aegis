- **A restarted server answers the page about a second sooner.** Importing
  the MCP library was most of a start. `aegis serve` now listens first and
  imports it in a thread, so a tab reconnects after about 1.8 s instead of
  2.7 s. An agent's call that arrives meanwhile waits for the MCP endpoint
  instead of failing.
