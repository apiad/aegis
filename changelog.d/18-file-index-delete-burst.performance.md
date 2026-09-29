- **A burst of deleted files no longer pins the daemon's CPU.** The file
  picker's index removed paths with a linear scan while holding the GIL: 3.6 ms
  per delete or rename on a 61k-path Workspace, even for a `.pyc` or a
  `.git/index.lock` that was never indexed. When an agent built and deleted a
  root filesystem inside the Workspace, the daemon spent more than 20 minutes
  draining the events, and every view and MCP call slowed down with it. Removal
  is now a binary search, and events under ignored paths are dropped before any
  stat. Replaying a 30k-file create-and-delete burst went from 52 s of CPU to
  7 s.
