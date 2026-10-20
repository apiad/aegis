- **`aegis init` proposes a cascade of every free OpenCode model, best
  first.** With OpenCode installed, the proposal is an agent `free` whose
  `models:` are the catalog's free models that can run an agent, ordered by
  the shipped ranking, unranked ones last, so a first config falls to the
  next free model when one runs out instead of stopping. Beside Claude Code
  it adds a `light` queue on `free` and `opus` stays the default; without
  it, `free` is the default and the `general` queue runs it. A paid model
  never joins the cascade; a catalog with no free model gets its best model
  alone, as before.
  Settings' Set up offer makes the same proposal.

help: config#cascades
