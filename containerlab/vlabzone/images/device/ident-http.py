#!/usr/bin/env python3
"""Identity page of a simulated device: who am I, and who is asking (peer)."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IDENTITY = {
    "switch": os.environ["SWITCH"],
    "device": os.environ["NAME"],
    "ip": os.environ["ADDR"],
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/blob/"):
            # /blob/<bytes>: payload for transfer tests (MTU, throughput)
            size = int(self.path.rsplit("/", 1)[1])
            self._send(b"x" * size, "application/octet-stream")
            return
        body = json.dumps({**IDENTITY, "peer": self.client_address[0]}).encode()
        self._send(body, "application/json")

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
