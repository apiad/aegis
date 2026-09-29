- **Known issues page, starting with scroll tearing on VTE terminals.** Rows
  that duplicate for a frame while scrolling in Ptyxis or GNOME Terminal come
  from VTE not implementing synchronized output, not from aegis. The new page
  explains the cause and the workaround (a terminal with mode 2026, such as
  Ghostty), and the README no longer says every frame is atomic.
