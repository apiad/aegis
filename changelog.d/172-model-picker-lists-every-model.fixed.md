- **The new tab's model picker lists every model Claude offers.** It was a
  text field with suggestions, prefilled with the agent's model, and Chromium
  shows only the suggestions that match the text, so the list held just that
  one model. It is now a dropdown of the models Claude Code lists for the
  directory (11 on 2.1.283, `default` included, against the four aliases aegis
  knew), with the agent's model marked `(current)`. The effort picker follows
  the chosen model: levels it does not take are disabled, and haiku, which
  takes none, disables it. (#172)
