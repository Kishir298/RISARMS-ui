"""Connection lifecycle manager: auth, auto-connect, heartbeat, backoff.

All blocking work is done by the caller's worker thread; this class never
touches Tk. State changes are delivered through registered callbacks.
"""
from __future__ import annotations

import logging
import random
import threading
import time

from .api import APIError, RemoteAPI
from .events import EventStream
from .states import ConnectionState

_LOG = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(
        self,
        api_factory=RemoteAPI,
        credentials=None,
        on_state=None,
        on_event=None,
        heartbeat_interval: float = 25.0,
        max_backoff: float = 30.0,
        renew_after: float = 6 * 3600,
        sleep=time.sleep,
        enable_events=True,
    ):
        self.api_factory = api_factory
        self.credentials = credentials
        self.on_state = on_state or (lambda state: None)
        self.on_event = on_event or (lambda event: None)
        self.heartbeat_interval = float(heartbeat_interval)
        self.max_backoff = float(max_backoff)
        self.renew_after = float(renew_after)
        self._activated = time.monotonic()
        self._sleep = sleep
        self._state = ConnectionState.OFFLINE
        self._api = None
        self._server = ""
        self._account = ""
        self._device_credential = ""
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._monitor = None
        self._events = None
        self._backoff = 0.5
        self.enable_events = bool(enable_events)

    @property
    def state(self) -> ConnectionState:
        with self._lock:
            return self._state

    @property
    def api(self):
        with self._lock:
            return self._api

    @property
    def profile(self):
        api = self.api
        return api.profile if api else None

    @property
    def server(self) -> str:
        return self._server

    def set_state(self, state: ConnectionState, detail: str = ""):
        with self._lock:
            changed = state is not self._state
            self._state = state
        if changed:
            try:
                self.on_state(state)
            except Exception:
                _LOG.exception("Connection state listener failed")
        if detail:
            _LOG.info("Connection state %s: %s", state.value, detail)

    def _publish(self, event: dict):
        try:
            self.on_event(event)
        except Exception:
            _LOG.exception("Connection event listener failed")

    def connect(self, server: str, username: str, password: str, remember: bool = True):
        self.disconnect(logout=False)
        self.set_state(ConnectionState.CONNECTING)
        api = self.api_factory(server)
        try:
            result = api.login(username, password)
        except APIError as exc:
            self._classify_login_error(exc)
            api.close()
            raise
        profile = result.get("profile") or {}
        if profile.get("role") == "pending":
            self.set_state(ConnectionState.PENDING_APPROVAL)
            api.close()
            return profile
        if profile.get("password_change_required"):
            self.set_state(ConnectionState.AUTH_EXPIRED, "password change required")
        self._activate(api, server, profile)
        credential = result.get("device_credential")
        if credential and remember and self.credentials is not None:
            self._device_credential = credential
            self.credentials.set(server, profile.get("id", username), credential)
        return profile

    def connect_device(self, server: str, account: str, credential: str):
        self.disconnect(logout=False)
        self.set_state(ConnectionState.CONNECTING)
        api = self.api_factory(server)
        try:
            result = api.login_device(credential)
        except APIError as exc:
            self._classify_login_error(exc)
            api.close()
            raise
        self._device_credential = credential
        self._activate(api, server, result.get("profile") or {}, account=account)
        return self.profile

    def _classify_login_error(self, exc: APIError):
        if exc.status == 403:
            self.set_state(ConnectionState.PENDING_APPROVAL)
        elif exc.status == 401:
            self.set_state(ConnectionState.AUTH_REJECTED)
        elif exc.status == 0:
            self.set_state(ConnectionState.SERVER_UNAVAILABLE)
        else:
            self.set_state(ConnectionState.AUTH_REJECTED)

    def _activate(self, api, server: str, profile: dict, account: str = ""):
        with self._lock:
            old = self._api
            self._api = api
            self._server = server
            self._account = account or str(profile.get("id", ""))
            self._stop.clear()
            self._backoff = 0.5
            self._activated = time.monotonic()
        if old is not None and old is not api:
            old.close()
        self.set_state(ConnectionState.ONLINE)
        if self.enable_events:
            self._start_events()
        if self._monitor is None or not self._monitor.is_alive():
            self._monitor = threading.Thread(target=self._monitor_loop, name="risarms-connection", daemon=True)
            self._monitor.start()

    def _start_events(self):
        api = self.api
        if api is None:
            return
        try:
            ticket = api.ws_ticket()
        except APIError:
            return
        token = ticket.get("ticket")
        if not token:
            return
        old = self._events
        if old is not None:
            old.stop()
        self._events = EventStream(
            self._server,
            token,
            on_event=self._on_event,
            on_close=self._on_event_close,
        )
        self._events.start()

    def _on_event(self, event: dict):
        kind = event.get("type", "")
        if kind == "session.revoked":
            self.set_state(ConnectionState.AUTH_REJECTED, "session revoked by server")
            self._stop.set()
        elif kind == "session.expiring":
            self.set_state(ConnectionState.RECONNECTING, "session expiring")
        self._publish(event)

    def _on_event_close(self, reason: str):
        if not self._stop.is_set() and self.state is ConnectionState.ONLINE:
            self.set_state(ConnectionState.RECONNECTING, "event channel closed")

    def _monitor_loop(self):
        while not self._stop.wait(self.heartbeat_interval):
            api = self.api
            if api is None:
                continue
            try:
                if time.monotonic() - self._activated >= self.renew_after:
                    api.renew()
                    with self._lock:
                        self._activated = time.monotonic()
                api.health()
                if self.state is ConnectionState.RECONNECTING:
                    self.set_state(ConnectionState.ONLINE, "heartbeat restored")
            except APIError as exc:
                if self._stop.is_set():
                    return
                if exc.status == 401:
                    self.set_state(ConnectionState.AUTH_EXPIRED)
                    if not self._try_renew():
                        self.set_state(ConnectionState.AUTH_EXPIRED)
                elif exc.status == 0:
                    self.set_state(ConnectionState.SERVER_UNAVAILABLE)
                else:
                    self.set_state(ConnectionState.RECONNECTING)

    def _try_renew(self) -> bool:
        api = self.api
        if api is None:
            return False
        try:
            api.renew()
            self.set_state(ConnectionState.ONLINE, "session renewed")
            return True
        except APIError as exc:
            if exc.status in (401, 403):
                return False
            self.set_state(ConnectionState.RECONNECTING)
            return False

    def reconnect(self):
        """Attempt reconnection using the remembered device credential."""
        api = self.api
        server, account = self._server, self._account
        if api is not None:
            server = api.url
        if not server:
            self.set_state(ConnectionState.OFFLINE)
            return False
        credential = self._device_credential
        if not credential and self.credentials is not None and account:
            credential = self.credentials.get(server, account) or ""
        if not credential:
            self.set_state(ConnectionState.OFFLINE, "no stored device credential")
            return False
        delay = min(self._backoff, self.max_backoff)
        self._backoff = min(self.max_backoff, self._backoff * 2)
        self.set_state(ConnectionState.RECONNECTING)
        self._sleep(delay + random.uniform(0, delay / 4))
        try:
            self.connect_device(server, account, credential)
            self._backoff = 0.5
            return True
        except APIError as exc:
            if exc.status in (401, 403):
                self.set_state(ConnectionState.AUTH_REJECTED)
            else:
                self.set_state(ConnectionState.SERVER_UNAVAILABLE)
            return False

    def disconnect(self, logout: bool = True):
        with self._lock:
            api = self._api
            self._api = None
            events = self._events
            self._events = None
            self._stop.set()
        if events is not None:
            events.stop()
        if api is not None:
            if logout:
                api.logout()
            api.close()
        self.set_state(ConnectionState.OFFLINE)

    def close(self):
        self.disconnect(logout=False)
