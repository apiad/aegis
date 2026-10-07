- **`aegis` alone opens aegis in its own window.** With no command it serves as
  `aegis serve` does and, once the port listens, opens the URL in a Chromium-family
  browser in app mode (`--app=`): no tabs, no address bar, no token in sight. Before,
  bare `aegis` failed with "Missing command" and the URL had to be pasted into a tab.
  If this root's server is already running, `aegis` opens a window on it and exits;
  a port held by anything else still fails. The browser is the desktop default when
  it is Chromium-family, else the first Chrome, Chromium, Edge or Brave on PATH, else
  the system browser; `--browser` or `AEGIS_BROWSER` overrides it. `aegis serve
  --window` does the same with serve's options.
