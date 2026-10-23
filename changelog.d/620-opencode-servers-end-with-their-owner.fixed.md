- **OpenCode servers no longer outlive what started them.** An `opencode serve`
  child survived its `aegis serve` being stopped or killed, a doctor or catalog
  probe cancelled while it started, and test servers stopped at teardown; at
  about 300 MB each, 81 of them took 24 GB and woke the OOM killer. The child
  now dies with the process that started it, a cancelled start or probe ends
  it, and `aegis serve` ends at start any OpenCode server whose owner is gone,
  including the ones earlier versions left behind.
