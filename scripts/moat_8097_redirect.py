#!/usr/bin/env python3
"""HU-2921 — :8097 retirement redirector.

The v1 moat demo server (:8097) is retired now that the Astro console at
:8100 is the single founder URL. Any hit on :8097 is redirected to the
console's live-chat pane so old founder bookmarks keep working.

Tailnet-only (binds 100.101.235.117). Runs under huible-moat-demo.service.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND_HOST = "100.101.235.117"
BIND_PORT = 8097
TARGET = "http://100.101.235.117:8100/huible-demo/chat"


class Handler(BaseHTTPRequestHandler):
    def _redirect(self) -> None:
        self.send_response(307 if self.command != "GET" else 302)
        self.send_header("Location", TARGET)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = do_HEAD = _redirect

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def main() -> None:
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    print(f"moat :8097 redirector -> {TARGET}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
