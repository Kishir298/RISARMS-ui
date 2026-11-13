"""Connection lifecycle manager: auth, auto-connect, heartbeat, backoff.

All blocking work is done by the caller's worker thread; this class never
touches Tk. State changes are delivered through registered callbacks.

The monitor thread owns recovery. A lost heartbeat, a dead control channel or a
transport failure schedules a single guarded reconnection attempt using the
stored device credential, with bounded exponential backoff. The manager reports
``ONLINE`` only when the heartbeat succeeds *and* the control channel it
previously held has been restored; an explicit rejection or revocation halts
automatic recovery and requires the user to authenticate again.
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

# Terminal states never retry on their own: the server has made a decision.
_HALT_STATES = (ConnectionState.AUTH_REJECTED, ConnectionState.PENDING_APPROVAL)


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
        self._events_expected = False
        self._backoff = 0.5
        self._retry_at = 0.0
        self._needs_reconnect = False
        self._halted = False
        self._renew_requested = False
        self._reconnect_lock = threading.Lock()
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
            self._retry_at = 0.0
            self._needs_reconnect = False
            self._halted = False
            self._activated = time.monotonic()
        if old is not None and old is not api:
            old.close()
        if self.enable_events:
            self._start_events()
            if self._events_expected and not self._event_channel_healthy():
                # The control channel is required once the server has issued a
                # ticket; do not claim Online while it is dead. A server without
                # the channel at all (_events_expected False) stays on polling.
                self._mark_disconnected("event channel unavailable")
            else:
                self.set_state(ConnectionState.ONLINE)
        else:
            self.set_state(ConnectionState.ONLINE)
        if self._monitor is None or not self._monitor.is_alive():
            self._monitor = threading.Thread(target=self._monitor_loop, name="risarms-connection", daemon=True)
            self._monitor.start()

    # ----------------------------------------------------------------- events
    def _start_events(self) -> bool:
        api = self.api
        if api is None:
            return False
        try:
            ticket = api.ws_ticket()
        except APIError:
            return False
        token = ticket.get("ticket")
        if not token:
            return False
        self._stop_event_stream()
        stream = EventStream(self._server, token, on_event=self._on_event)

        def closed(reason: str, _stream=stream):
            self._on_event_close(reason, _stream)

        stream.on_close = closed
        with self._lock:
            self._events = stream
            self._events_expected = True
        stream.start()
        return True

    def _stop_event_stream(self):
        with self._lock:
            stream = self._events
            self._events = None
        if stream is not None:
            stream.stop()

    def _event_channel_healthy(self) -> bool:
        stream = self._events
        return bool(stream is not None and stream.alive)

    def _on_event(self, event: dict):
        kind = event.get("type", "")
        if kind == "session.revoked":
            self._halted = True
            self._needs_reconnect = False
            self._stop_event_stream()
            self._close_api()
            self.set_state(ConnectionState.AUTH_REJECTED, "session revoked by server")
        elif kind == "session.expiring":
            # Ask the monitor to renew on its next tick instead of dropping the
            # session; the heartbeat still proves the server is reachable.
            self._renew_requested = True
        self._publish(event)

    def _on_event_close(self, reason: str, stream=None):
        if stream is not None and stream is not self._events:
            return  # a channel we deliberately replaced
        if self._stop.is_set() or self._halted:
            return
        if self.state in (ConnectionState.ONLINE, ConnectionState.RECONNECTING):
            self._mark_disconnected("event channel closed")

    # -------------------------------------------------------------- recovery
    def _close_api(self):
        with self._lock:
            api = self._api
            self._api = None
        if api is not None:
            try:
                api.close()
            except Exception:
                _LOG.exception("Closing the API client failed")

    def _schedule_retry(self):
        with self._lock:
            delay = min(self._backoff, self.max_backoff)
            self._backoff = min(self.max_backoff, max(self._backoff * 2, 0.5))
            self._retry_at = time.monotonic() + delay + random.uniform(0, delay / 4)
            self._needs_reconnect = True

    def _mark_disconnected(self, detail: str, unavailable: bool = False):
        with self._lock:
            if self._halted:
                return
            already_scheduled = self._needs_reconnect
            self._needs_reconnect = True
        if not already_scheduled:
            self._schedule_retry()
        self._stop_event_stream()
        self.set_state(
            ConnectionState.SERVER_UNAVAILABLE if unavailable else ConnectionState.RECONNECTING,
            detail,
        )

    def _halt(self, state: ConnectionState, detail: str):
        with self._lock:
            self._halted = True
            self._needs_reconnect = False
        self._stop_event_stream()
        self._close_api()
        self.set_state(state, detail)
        self._stop.set()

    def _handle_failure(self, exc: APIError):
        if exc.status in (401, 403):
            state = ConnectionState.AUTH_EXPIRED if exc.status == 401 else ConnectionState.AUTH_REJECTED
            self._halt(state, str(exc))
        elif exc.status == 0:
            self._mark_disconnected(str(exc), unavailable=True)
        else:
            self._mark_disconnected(str(exc))

    def _ensure_online(self, detail: str = ""):
        """Report Online only when the control channel is actually usable."""
        if self.enable_events and self._events_expected and not self._event_channel_healthy():
            started = self._start_events()
            if not started or not self._event_channel_healthy():
                self._mark_disconnected("event channel unavailable")
                return
        self.set_state(ConnectionState.ONLINE, detail)

    def _attempt_reconnect(self) -> bool:
        with self._lock:
            if self._halted:
                return False
        if not self._reconnect_lock.acquire(blocking=False):
            return False  # an attempt is already in flight
        try:
            server, account = self._server, self._account
            credential = self._device_credential
            if not credential and self.credentials is not None and account:
                credential = self.credentials.get(server, account) or ""
            if not server or not credential:
                self._halt(ConnectionState.OFFLINE, "no stored device credential")
                return False
            self.set_state(ConnectionState.RECONNECTING, "reconnecting with device credential")
            try:
                self.connect_device(server, account, credential)
            except APIError as exc:
                self._handle_failure(exc)
                return False
            except Exception:
                _LOG.exception("Reconnection failed")
                self._mark_disconnected("reconnection failed")
                return False
            with self._lock:
                self._backoff = 0.5
                self._retry_at = 0.0
                self._needs_reconnect = False
            self._ensure_online("reconnected")
            self._publish({"type": "connection.restored", "server": server})
            return True
        finally:
            if not self._halted:
                self._stop.clear()
            self._reconnect_lock.release()

    def _monitor_loop(self):
        tick = min(self.heartbeat_interval, 1.0)
        while not self._stop.wait(tick):
            with self._lock:
                if self._halted:
                    return
                needs_reconnect = self._needs_reconnect
                retry_at = self._retry_at
            if needs_reconnect:
                if time.monotonic() >= retry_at:
                    self._attempt_reconnect()
                continue
            api = self.api
            if api is None:
                self._mark_disconnected("connection lost")
                continue
            try:
                self._heartbeat(api)
            except APIError as exc:
                if self._stop.is_set():
                    return
                self._handle_failure(exc)
                continue
            except Exception:
                _LOG.exception("Heartbeat failed unexpectedly")
                self._mark_disconnected("heartbeat error")
                continue
            self._ensure_online("heartbeat ok")

    def _heartbeat(self, api):
        renew = self._renew_requested
        if not renew and self.renew_after and time.monotonic() - self._activated >= self.renew_after:
            renew = True
        if renew:
            self._renew_requested = False
            result = api.renew() or {}
            with self._lock:
                self._activated = time.monotonic()
            profile = result.get("profile")
            if profile:
                self._publish({"type": "session.refreshed", "profile": profile})
        api.health()

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
            self._mark_disconnected("session renewal failed")
            return False

    def reconnect(self) -> bool:
        """Attempt one immediate reconnection using the remembered device credential.

        Backoff scheduling is owned by the monitor thread; this entry point is
        for an operator- or UI-triggered retry and never runs concurrently with
        a scheduled attempt.
        """
        with self._lock:
            if self._halted and self._state in _HALT_STATES:
                return False
            self._needs_reconnect = True
            self._retry_at = time.monotonic()
        return self._attempt_reconnect()

    def disconnect(self, logout: bool = True):
        with self._lock:
            api = self._api
            self._api = None
            events = self._events
            self._events = None
            self._events_expected = False
            self._needs_reconnect = False
            self._halted = True
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
