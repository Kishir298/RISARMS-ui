"""OS-backed credential storage for scoped, revocable device credentials.

Only server-issued device credentials belong here. Passwords and session
tokens must never be written to disk or to preference files.
"""
from __future__ import annotations

import logging

SERVICE = "risarms-ui"
_LOG = logging.getLogger(__name__)


class CredentialStore:
    """Small wrapper around the operating system keychain.

    If a backend is unavailable the store degrades to memory-only and
    reports ``persistent is False``. Callers must then require a fresh
    login instead of writing secrets to JSON.
    """

    def __init__(self, service: str = SERVICE, backend=None):
        self.service = service
        self._memory: dict[str, str] = {}
        self._backend = backend
        self._loaded = backend is not None
        self.persistent = backend is not None

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            import keyring

            self._backend = keyring
            self.persistent = True
        except Exception:  # keyring missing or no usable backend
            _LOG.info("OS credential storage unavailable; credentials stay in memory")
            self._backend = None
            self.persistent = False

    def available(self) -> bool:
        self._load()
        return bool(self.persistent)

    @staticmethod
    def _key(server: str, account: str) -> str:
        return f"{server.rstrip('/')}|{account}"

    def get(self, server: str, account: str) -> str | None:
        if not server or not account:
            return None
        key = self._key(server, account)
        if key in self._memory:
            return self._memory[key]
        self._load()
        if self._backend is None:
            return None
        try:
            return self._backend.get_password(self.service, key)
        except Exception:
            return None

    def set(self, server: str, account: str, credential: str) -> bool:
        if not server or not account or not credential:
            raise ValueError("Server, account and credential are required.")
        key = self._key(server, account)
        self._load()
        self._memory[key] = credential
        if self._backend is None:
            return False
        try:
            self._backend.set_password(self.service, key, credential)
            return True
        except Exception:
            _LOG.warning("Could not persist device credential in the OS keychain")
            self.persistent = False
            return False

    def delete(self, server: str, account: str) -> None:
        key = self._key(server, account)
        self._memory.pop(key, None)
        self._load()
        if self._backend is None:
            return
        try:
            self._backend.delete_password(self.service, key)
        except Exception:
            pass
