- **The new-tab model chip lists every model the agent's harness can run.** It
  offered four fixed Claude aliases plus the models `.aegis.yaml` names, which
  missed most of what Claude Code lists and nothing at all for OpenCode, whose
  153 models never appeared. The chip now reads the harness's own catalog, the
  one the server validates a model change against, and type-ahead filters it.
  The agent's model stays marked as current. (#172, #241)
