- **`aegis2 serve`, the first slice of the web-native rewrite (experimental).**
  One Claude Code session in a browser tab: spawn it from a profile in
  `.aegis.yaml`, watch the transcript stream in with tool rows, failures and
  rendered Markdown, steer it mid-turn, interrupt it with Esc, and close it.
  Three themes (Ink, Logbook, Syalia). It listens on 127.0.0.1:8742, wants the
  token it prints, keeps its own state in `.aegis2/`, and runs next to `aegis`
  without touching it. The session ends when the server stops; resume comes in
  slice 2.
