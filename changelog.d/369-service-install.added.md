- **`aegis service install` writes the systemd unit, and `aegis doctor` checks
  an installed one.** The unit's settings (`KillMode=mixed`,
  `OOMPolicy=continue`, memory caps sized to the host, a negative
  `OOMScoreAdjust`) were copied by hand from the README, and a hand-written
  unit drifted from them with nothing reporting it. `install` asks for the
  user, port, root and memory caps with defaults from the host, takes each as
  a flag, and prints the `sudo` commands rather than running them;
  `--dry-run` prints the unit. The doctor warns on each setting a live aegis
  unit sets differently.
help: service
