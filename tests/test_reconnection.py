"""Regression tests for automatic reconnection after the control channel drops.

Before this fix the heartbeat loop recorded a failure state but never invoked
reconnection, so a sleeping laptop or dropped network stayed permanently
"Reconnecting" while the stale event stream kept running.
"""
from __future__ import annotations

import threading
import time

from risarms_ui.connection import manager as manager_module
from risarms_ui.connection.api import APIError
from risarms_ui.connection.manager import ConnectionManager
from risarms_ui.connection.states import ConnectionState
from risarms_ui.security.credentials import CredentialStore


class MemoryBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, name):
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        self.values[(service, name)] = value

    def delete_password(self, service, name):
        self.values.pop((service, name), None)


class RecoveringAPI:
    """Backend that fails health checks, then accepts a device re-login."""

    def __init__(self, url, health_failures=1, reject_device=False):
        self.url = url
        self.profile = {"id": "u1", "role": "owner", "name": "Owner"}
        self.csrf = "csrf"
        self.health_failures = health_failures
        self.reject_device = reject_device
        self.health_calls = 0
        self.device_logins = 0
        self.closed = False

    def login(self, username, password):
        return {"profile": self.profile, "csrf": "csrf", "device_credential": "device-token"}

    def login_device(self, credential):
        self.device_logins += 1
        if self.reject_device:
            raise APIError("device rejected", 401)
        return {"profile": self.profile, "csrf": "csrf"}

    def ws_ticket(self):
        return {"ticket": "single-use"}

    def health(self):
        self.health_calls += 1
        if self.health_calls <= self.health_failures:
            raise APIError("CORE server is unavailable", 0)
        return {"service": "CORE"}

    def renew(self):
        return {"profile": self.profile}

    def logout(self):
        pass

    def close(self):
        self.closed = True


def make_manager(api, **overrides):
    states = []
    options = dict(
        api_factory=lambda url: api,
        credentials=CredentialStore(backend=MemoryBackend()),
        on_state=states.append,
        heartbeat_interval=0.02,
        max_backoff=0.02,
        sleep=lambda seconds: None,
        enable_events=False,
    )
    options.update(overrides)
    manager = ConnectionManager(**options)
    return manager, states


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_heartbeat_failure_reconnects_with_device_credential_and_reports_online():
    api = RecoveringAPI("https://core.example", health_failures=1)
    manager, states = make_manager(api)
    manager.connect("https://core.example", "owner", "password")
    try:
        assert wait_for(lambda: api.device_logins >= 1), "heartbeat never triggered reconnection"
        assert wait_for(lambda: manager.state is ConnectionState.ONLINE)
        assert ConnectionState.RECONNECTING in states, "state never showed a reconnect attempt"
        assert manager.credentials.get("https://core.example", "u1") == "device-token"
    finally:
        manager.close()


def test_retry_is_not_run_concurrently():
    api = RecoveringAPI("https://core.example")
    manager, _ = make_manager(api)
    manager.connect("https://core.example", "owner", "password")
    try:
        # Hold the guard exactly as an in-flight attempt would.
        assert manager._reconnect_lock.acquire(blocking=False)
        try:
            assert manager._attempt_reconnect() is False
        finally:
            manager._reconnect_lock.release()
        assert api.device_logins == 0
    finally:
        manager.close()


def test_rejected_device_credential_halts_automatic_retry():
    api = RecoveringAPI("https://core.example", health_failures=1, reject_device=True)
    manager, _ = make_manager(api)
    manager.connect("https://core.example", "owner", "password")
    try:
        assert wait_for(lambda: manager.state is ConnectionState.AUTH_EXPIRED)
        attempts = api.device_logins
        time.sleep(0.15)
        assert api.device_logins == attempts, "kept retrying after explicit rejection"
        assert manager.reconnect() is False
    finally:
        manager.close()


def test_backoff_is_bounded():
    api = RecoveringAPI("https://core.example")
    manager, _ = make_manager(api, max_backoff=1.0)
    manager._backoff = 0.5
    try:
        for _ in range(12):
            manager._schedule_retry()
            assert manager._backoff <= 1.0
        delay = manager._retry_at - time.monotonic()
        assert 0 < delay <= 1.0 + 0.25  # bounded delay plus at most 25% jitter
    finally:
        manager.close()


def test_missing_event_channel_is_never_reported_online(monkeypatch):
    class DeadStream:
        def __init__(self, *args, **kwargs):
            self.stopped = False

        def start(self):
            pass

        def stop(self):
            self.stopped = True

        @property
        def alive(self):
            return False

    api = RecoveringAPI("https://core.example", health_failures=0)
    manager, _ = make_manager(api, enable_events=True)
    monkeypatch.setattr(manager_module, "EventStream", DeadStream)
    try:
        manager.connect("https://core.example", "owner", "password")
        # connect() starts the channel; it is dead, so Online must not stand.
        assert manager.state is not ConnectionState.ONLINE
        manager._ensure_online()
        assert manager.state is ConnectionState.RECONNECTING
    finally:
        manager.close()


def test_event_channel_close_schedules_recovery():
    api = RecoveringAPI("https://core.example", health_failures=0)
    manager, _ = make_manager(api)
    manager.connect("https://core.example", "owner", "password")
    try:
        assert manager.state is ConnectionState.ONLINE
        manager._on_event_close("closed")
        assert manager.state is ConnectionState.RECONNECTING
        assert manager._needs_reconnect is True
    finally:
        manager.close()


def test_stale_channel_close_is_ignored():
    api = RecoveringAPI("https://core.example", health_failures=0)
    manager, _ = make_manager(api)
    manager.connect("https://core.example", "owner", "password")
    try:
        stale = object()  # not the current stream
        manager._on_event_close("closed", stale)
        assert manager.state is ConnectionState.ONLINE
    finally:
        manager.close()


def test_revocation_event_closes_the_api_and_requires_sign_in():
    api = RecoveringAPI("https://core.example", health_failures=0)
    manager, _ = make_manager(api)
    manager.connect("https://core.example", "owner", "password")
    try:
        manager._on_event({"type": "session.revoked", "message": "revoked"})
        assert manager.state is ConnectionState.AUTH_REJECTED
        assert manager.api is None
        assert api.closed is True
        assert manager._halted is True
    finally:
        manager.close()


def test_heartbeat_loop_survives_a_failed_reconnect_attempt():
    """A failed attempt must not kill the monitor thread (the old bug)."""
    api = RecoveringAPI("https://core.example", health_failures=1, reject_device=False)
    manager, _ = make_manager(api)
    manager.connect("https://core.example", "owner", "password")
    try:
        assert wait_for(lambda: manager.state is ConnectionState.ONLINE)
        monitor = manager._monitor
        assert monitor is not None and monitor.is_alive()
        assert threading.current_thread() is not monitor
    finally:
        manager.close()
