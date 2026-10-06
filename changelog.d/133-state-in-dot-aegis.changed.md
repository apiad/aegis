- **State lives in `.aegis/state/`.** 2.0.0 kept it in a directory of its own
  next to the legacy tree's. `aegis serve` now refuses to start on a
  `.aegis/state/` that still holds the legacy tree's state (`daemon.lock`,
  `workspace.json`, `history_index.json`, `comms/`), and prints the one `mv`
  that moves it to `.aegis/legacy-state/`. Move your 2.0.0 state into
  `.aegis/state/` once the legacy one is out of the way.
