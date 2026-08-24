# --------------------------------------------------
# agentic_browser_v2 / webhook_server.py
# --------------------------------------------------
"""Minimal Meta webhook endpoint for local Instagram messaging tests.

Run this server locally, then expose port 8000 with ``ngrok http 8000``.
Configure Meta with the public URL plus ``/meta/webhook`` and the same
META_WEBHOOK_VERIFY_TOKEN set in .env.
"""

import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv


load_dotenv()

WEBHOOK_PATH = "/meta/webhook"
VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
PORT = int(os.environ.get("META_WEBHOOK_PORT", "8000"))


class MetaWebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        request = urlparse(self.path)
        query = parse_qs(request.query)
        mode = query.get("hub.mode", [""])[0]
        token = query.get("hub.verify_token", [""])[0]
        challenge = query.get("hub.challenge", [""])[0]

        if (
            request.path == WEBHOOK_PATH
            and VERIFY_TOKEN
            and mode == "subscribe"
            and hmac.compare_digest(token, VERIFY_TOKEN)
        ):
            self._respond(HTTPStatus.OK, challenge, content_type="text/plain")
            print("[webhook] Meta verification succeeded.")
            return

        self._respond(HTTPStatus.FORBIDDEN, "Verification failed.", content_type="text/plain")

    def do_POST(self) -> None:
        request = urlparse(self.path)
        if request.path != WEBHOOK_PATH:
            self._respond(HTTPStatus.NOT_FOUND, "Not found.", content_type="text/plain")
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body)
            entry_count = len(payload.get("entry", [])) if isinstance(payload, dict) else 0
            print(f"[webhook] Received Meta event with {entry_count} entr{'y' if entry_count == 1 else 'ies'}.")
        except json.JSONDecodeError:
            print("[webhook] Received a non-JSON Meta event.")

        # Meta requires a prompt 200 response so it does not retry delivery.
        self._respond(HTTPStatus.OK, "EVENT_RECEIVED", content_type="text/plain")

    def log_message(self, format: str, *args) -> None:
        """Keep the console focused on webhook events instead of HTTP access logs."""

    def _respond(self, status: HTTPStatus, body: str, content_type: str) -> None:
        encoded_body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded_body)))
        self.end_headers()
        self.wfile.write(encoded_body)


def main() -> None:
    if not VERIFY_TOKEN:
        raise RuntimeError(
            "META_WEBHOOK_VERIFY_TOKEN is not set. Add a long random value to .env first."
        )

    server = ThreadingHTTPServer(("0.0.0.0", PORT), MetaWebhookHandler)
    print(f"Webhook server listening on http://127.0.0.1:{PORT}{WEBHOOK_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWebhook server stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
