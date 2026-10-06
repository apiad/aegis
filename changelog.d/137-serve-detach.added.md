- **`aegis serve -d` runs in the background.** `--detach` starts the server in its
  own session, so it survives the terminal and SSH hangups, and returns once the
  port listens, printing the URL with its token, the pid and the `kill` that stops
  it. Output goes to `.aegis/state/serve.log` and the pid to `.aegis/state/serve.pid`.
  Before, a remote host needed tmux, screen or a hand-rolled `setsid nohup`, which
  hid boot errors until someone read the log; now a server that dies before
  listening fails the command with the end of its log.
