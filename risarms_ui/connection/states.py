"""Explicit connection states shown by the desktop application."""
from __future__ import annotations

from enum import Enum


class ConnectionState(str, Enum):
    ONLINE = "online"
    CONNECTING = "connecting"
    RECONNECTING = "reconnecting"
    OFFLINE = "offline"
    AUTH_EXPIRED = "authentication_expired"
    AUTH_REJECTED = "authentication_rejected"
    PENDING_APPROVAL = "pending_approval"
    SYNC_PENDING = "synchronization_pending"
    SERVER_UNAVAILABLE = "server_unavailable"

    @property
    def label(self) -> str:
        return {
            ConnectionState.ONLINE: "Online",
            ConnectionState.CONNECTING: "Connecting",
            ConnectionState.RECONNECTING: "Reconnecting",
            ConnectionState.OFFLINE: "Offline",
            ConnectionState.AUTH_EXPIRED: "Authentication expired",
            ConnectionState.AUTH_REJECTED: "Authentication rejected",
            ConnectionState.PENDING_APPROVAL: "Pending approval",
            ConnectionState.SYNC_PENDING: "Synchronization pending",
            ConnectionState.SERVER_UNAVAILABLE: "Server unavailable",
        }[self]

    @property
    def connected(self) -> bool:
        return self is ConnectionState.ONLINE
