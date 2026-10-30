"""``ssh``'s ProxyCommand through an exit (exit.py): stdin and stdout become one
stream to ``host port`` through the proxy that ``HTTPS_PROXY`` names, so
``git`` over SSH leaves from the far server's address too.

Runs as a file, with ``python -I``, on every ``git fetch``: it imports only the
standard library, so it starts fast and needs nothing else from aegis.
"""

import base64
import os
import socket
import sys
import threading
from urllib.parse import unquote, urlsplit


def open_tunnel(proxy: str, host: str, port: int) -> socket.socket:
    u = urlsplit(proxy)
    sock = socket.create_connection((u.hostname, u.port), 30)
    sock.settimeout(None)
    target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    auth = base64.b64encode(
        f"{unquote(u.username or '')}:{unquote(u.password or '')}".encode()
    ).decode()
    sock.sendall(
        f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
        f"Proxy-Authorization: Basic {auth}\r\n\r\n".encode()
    )
    head = b""
    while b"\r\n\r\n" not in head:
        got = sock.recv(1)
        if not got:
            raise ConnectionError("the exit closed the connection")
        head += got
    status = head.split(b"\r\n", 1)[0].decode("latin-1")
    if status.split(" ")[1:2] != ["200"]:
        body = sock.recv(4096).decode("latin-1").strip()
        raise ConnectionError(body or status)
    return sock


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: exit_connect.py host port", file=sys.stderr)
        return 2
    proxy = os.environ.get("HTTPS_PROXY", "")
    if not proxy:
        print("exit_connect: HTTPS_PROXY is not set", file=sys.stderr)
        return 2
    try:
        sock = open_tunnel(proxy, argv[1], int(argv[2]))
    except (OSError, ValueError) as e:
        print(f"exit_connect: {e}", file=sys.stderr)
        return 1

    def up() -> None:
        try:
            while data := os.read(0, 65536):
                sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    threading.Thread(target=up, daemon=True).start()
    try:
        while data := sock.recv(65536):
            os.write(1, data)
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
