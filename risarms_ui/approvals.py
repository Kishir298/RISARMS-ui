"""Loopback approval broker for local agent tool calls."""
from __future__ import annotations

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

MAX_BODY = 64 * 1024
DECISIONS = {"allow_once", "allow_session", "deny"}


class ApprovalBroker:
    def __init__(self, request, timeout=120):
        self.request = request
        self.timeout = timeout
        self.token = secrets.token_urlsafe(32)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="risarms-approval")
        self.thread.start()

    def env(self, job_id=""):
        return {
            "RISARMS_APPROVAL_URL": f"http://127.0.0.1:{self.port}/approve",
            "RISARMS_APPROVAL_TOKEN": self.token,
            "RISARMS_APPROVAL_JOB": str(job_id),
            "AGENT_APPROVAL": "1",
            "ASIS_APPROVAL_BROKER": "1",
        }

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _json(self, status, payload):
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                if urlparse(self.path).path != "/approve":
                    self._json(404, {"error": "Unknown endpoint."})
                    return
                if not hmac.compare_digest(self.headers.get("X-RISARMS-Approval-Token", ""), broker.token):
                    self._json(403, {"error": "Approval token rejected."})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json(400, {"error": "Invalid length."})
                    return
                if length < 2 or length > MAX_BODY:
                    self._json(413, {"error": "Approval request too large."})
                    return
                try:
                    payload = json.loads(self.rfile.read(length))
                except (ValueError, OSError):
                    self._json(400, {"error": "Invalid approval request."})
                    return
                if not isinstance(payload, dict):
                    self._json(400, {"error": "Invalid approval request."})
                    return
                try:
                    result = broker.request(payload)
                except Exception:
                    result = {"decision": "deny", "reason": "approval unavailable"}
                decision = result.get("decision") if isinstance(result, dict) else "deny"
                if decision not in DECISIONS:
                    decision = "deny"
                self._json(200, {"decision": decision, "reason": str(result.get("reason", ""))[:500]})

        return Handler
