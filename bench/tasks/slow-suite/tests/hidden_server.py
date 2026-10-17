"""A local stand-in for the inventory gateway, with scripted failures.

    server.script("/orders", [503, 503, 200])   # fail twice, then answer
    server.script("/orders", ["reset", 200])    # drop the connection once

Each request to a scripted path takes the next entry; the last one repeats. An
unscripted path answers 200. Every answer echoes the method, path and JSON body.
`server.hits[path]` counts requests per path, query string removed.
"""

import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeServer:
    def __init__(self):
        self.scripts: dict[str, list] = {}
        self.hits: Counter = Counter()
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self):
                path = self.path.split("?", 1)[0]
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                with outer.lock:
                    outer.hits[path] += 1
                    script = outer.scripts.get(path, [200])
                    step = script[0] if len(script) == 1 else script.pop(0)
                if step == "reset":
                    self.close_connection = True
                    self.connection.close()
                    return
                if path.startswith("/items"):
                    page = int(self.path.split("page=")[1].split("&")[0])
                    data = {"items": [f"item-{page}-{i}" for i in range(3)], "next": page < 3}
                else:
                    data = {"method": self.command, "path": path, "body": body,
                            "auth": self.headers.get("Authorization")}
                out = json.dumps(data).encode()
                self.send_response(step)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = do_PUT = do_DELETE = _serve

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def script(self, path: str, steps: list) -> None:
        with self.lock:
            self.scripts[path] = list(steps)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
