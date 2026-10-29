- **`aegis map` no longer hangs when a grammar download stalls.** On a
  network that accepted the connection and never answered, the first run
  printed nothing and never ended. aegis now needs wayfind 0.1.1, which
  fetches missing grammars first, gives up on one after 120 s, names it, and
  skips its files until a later run can download it.
