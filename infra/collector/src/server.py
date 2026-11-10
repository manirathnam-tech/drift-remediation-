"""HTTP entry point for the in-cluster drift collector."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from collector import handler
from queue_worker import run_supervised

MAX_REQUEST_BYTES = 128 * 1024


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

        if self.headers.get("Transfer-Encoding"):
            self._respond(400, {"error": "transfer encoding is not supported"})
            return

        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except ValueError:
            self._respond(400, {"error": "invalid content length"})
            return
        if length <= 0:
            self._respond(400, {"error": "content length is required"})
            return
        if length > MAX_REQUEST_BYTES:
            self._respond(413, {"error": "request body exceeds the 128 KiB limit"})
            return

        try:
            raw_body = self.rfile.read(length)
            if len(raw_body) != length:
                self._respond(400, {"error": "request body is incomplete"})
                return
            body = raw_body.decode("utf-8")
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
        except UnicodeDecodeError:
            self._respond(400, {"error": "request body must be UTF-8"})
        except Exception:    # noqa: BLE001
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
    threading.Thread(target=run_supervised, name="sqs-consumer", daemon=True).start()
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), CollectorHandler).serve_forever()
