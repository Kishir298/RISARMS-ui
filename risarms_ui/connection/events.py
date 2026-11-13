"""Authenticated WebSocket control channel client.

The connection ticket is short-lived and single-use. It is never logged or
written to disk. TLS verification is enabled by default.
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
from urllib.parse import urlparse

_LOG = logging.getLogger(__name__)


def websocket_url(origin: str, path: str = "/api/ws") -> str:
    parsed = urlparse(origin)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}{path}"


class EventStream:
    """Runs a WebSocket reader in a daemon thread."""

    def __init__(self, origin: str, ticket: str, on_event=None, on_close=None, ssl_context=None):
        self.origin = origin
        self.ticket = ticket
        self.on_event = on_event or (lambda event: None)
        self.on_close = on_close or (lambda reason: None)
        self.ssl_context = ssl_context
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._socket = None

    @property
    def alive(self) -> bool:
        """True while the reader thread is still holding a usable channel."""
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="risarms-events", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            from websockets.sync.client import connect

            url = websocket_url(self.origin) + "?ticket=" + self.ticket
            context = self.ssl_context
            if context is None and url.startswith("wss://"):
                context = ssl.create_default_context()
            with connect(url, ssl=context, open_timeout=10, close_timeout=5) as socket:
                self._socket = socket
                while not self._stop.is_set():
                    try:
                        raw = socket.recv(timeout=1.0)
                    except TimeoutError:
                        continue
                    if raw is None:
                        break
                    try:
                        event = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(event, dict):
                        self.on_event(event)
                    if self._stop.is_set():
                        break
        except Exception as exc:  # network, TLS, import or handshake failure
            _LOG.info("Event channel closed: %s", type(exc).__name__)
            self.on_close(type(exc).__name__)
            return
        self.on_close("closed")

    def stop(self):
        self._stop.set()
        socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
