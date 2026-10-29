"""HTTP entry point for the in-cluster drift collector."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from collector import handler


class CollectorHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/events":
            self._respond(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            result = handler({
                "headers": dict(self.headers.items()),
                "body": body,
                "isBase64Encoded": False,
            })
            self._respond(
                result.get("statusCode", 500),
                json.loads(result.get("body", "{}")),
                result.get("headers", {}),
            )
        except Exception:
            self._respond(500, {"error": "collector request failed"})

    def _respond(self, status, payload, headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            if key.lower() != "content-type":
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), CollectorHandler).serve_forever()
