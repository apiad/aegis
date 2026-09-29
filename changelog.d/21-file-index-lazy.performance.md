- **The file index starts when first used and stops watching when idle.**
  Every view used to walk the whole cwd at mount and keep a watchdog observer
  on it for the life of the daemon: 100,953 inotify watches on the Workspace,
  and about 11% of a core of event processing with 12 agents working, whether
  or not anyone opened the picker. The index is now built by the first
  Ctrl+O, file browser tab, or Ctrl+click or Alt+click on a path. The
  observer stops after 10 minutes without one of those, and the next query
  answers from the stale index while a re-walk refreshes it.
