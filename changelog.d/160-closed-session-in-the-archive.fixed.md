- **A closed session shows in the Fleet's archive at once.** The archive was
  read before the Close and nothing read it again, so the session appeared only
  after a reload. The client now rereads the archive when a session leaves the
  open list, wherever it was closed (#160).
