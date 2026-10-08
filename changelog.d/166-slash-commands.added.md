- **Slash commands in the composer.** `/model`, `/effort` and `/permission` switch a
  running session through Claude's control requests and survive a resume; `/rename`,
  `/title`, `/stop` and `/close` act on the session; Claude's own commands, skills
  and `.claude/commands` pass through. Typing `/` or pressing Alt+/ opens a menu that
  completes names, models and effort levels. An unknown command is refused instead
  of being sent to the model as a paid prompt.
