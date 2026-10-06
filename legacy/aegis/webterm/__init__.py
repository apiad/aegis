"""`aegis web`: browsers as clients of the daemon's unix socket.

A browser tab gets one view, exactly as a terminal does, and this package
relays that view's frames between the tab's WebSocket and the socket. It
checks the token at its front door, which is the only door that faces a
network; the daemon behind it binds no web port.

What it must never learn is what a session, an agent or a queue is. The
retired web layer knew, and its protocol grew a message per feature until
it fell behind the TUI. tests/webterm/test_imports.py holds the line.
"""
