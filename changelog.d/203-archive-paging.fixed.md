- **The archive shows every closed session, not the newest 50.** The client
  never asked for a second page, so on a server with 110 sessions 60 could not
  be reached, and the timestamp cursor skipped a session that shared its time
  with the last row of a page. The Fleet now says `Showing 50 of 120` and
  pages with Show 50 more; `archive.list` takes a cursor and returns the page,
  the total and a count per server.
