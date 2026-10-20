- **An agent can name an ordered list of models.** `models:` in `.aegis.yaml`
  makes a cascade: sessions, queue workers, workflow nodes and schedules start
  on the first model that is not cooling and whose quota gate is open, and a
  turn that fails on quota, a rate limit, an overload or a model that left the
  provider falls to the next model and continues the same turn. The
  transcript says when it falls, the card shows the model's place in the
  cascade, and a model that failed cools so new sessions skip it. `model:`
  agents work as before.

help: config#cascades
