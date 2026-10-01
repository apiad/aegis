- **Ctrl+W, leaving F10 and quitting no longer close the TUI.** The brain
  calls widget observers from its own tasks, which carry no Textual
  `active_app` or another view's, so a timer a widget armed there (the tool
  spinner, F10's redraw) died on its first tick. Removing the widget then
  re-raised that `LookupError` and the view exited. Every callback a widget
  subscribes on the brain now runs as the widget's own app.
