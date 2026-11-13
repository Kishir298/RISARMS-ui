from __future__ import annotations

import platform
import socket
import tkinter as tk
from tkinter import ttk

from .. import __version__
from ..widgets import Card, button
from .base import Page


class DevicesPage(Page):
    title = "Devices"

    def build(self):
        ttk.Label(self.body, text="Devices", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="Local identity and CORE-registered devices.", style="Muted.TLabel").pack(
            anchor="w", pady=(4, 12)
        )
        button(self.body, "Refresh registered devices", self.refresh, accent=True)

        local = Card(self.body, "Current device", self.colors)
        self.local_text = tk.StringVar()
        ttk.Label(local.body, textvariable=self.local_text, style="Surface.TLabel", wraplength=820).pack(anchor="w")
        self.local_text.set(
            f"Hostname: {socket.gethostname()}\nPlatform: {platform.system()} {platform.release()} ({platform.machine()})\n"
            f"Application version: {__version__}\nLocal profile: {self.app.profile_name()}"
        )

        remote = Card(self.body, "Registered devices", self.colors)
        self.remote_text = tk.StringVar(value="Connect to CORE-HOST to list registered devices.")
        ttk.Label(remote.body, textvariable=self.remote_text, style="Surface.TLabel", wraplength=820).pack(anchor="w")
        self.refresh()

    def refresh(self):
        if self.app.connection.state.name != "ONLINE":
            self.remote_text.set("Offline. Device registration and removal require CORE-HOST.")
            return

        def work():
            return self.app.require_remote().request("GET", "/api/devices")

        def done(data):
            devices = data.get("devices", data if isinstance(data, list) else [])
            if not devices:
                self.remote_text.set("No registered devices were returned.")
                return
            self.remote_text.set("\n".join(
                f"• {item.get('device_name') or item.get('device_id')} · {item.get('device_type', '—')} · "
                f"{item.get('platform', '—')} · {item.get('status', item.get('last_seen', '—'))}"
                for item in devices
            ))

        def error(exc):
            self.remote_text.set(f"Device list unavailable: {exc}")

        self.app.run_async(work, done, error)
