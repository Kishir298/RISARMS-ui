from __future__ import annotations

import json
import platform
import socket
import tkinter as tk
from tkinter import ttk

from .. import APP_NAME, __version__
from ..widgets import Card, button, row
from .base import Page


class HomePage(Page):
    title = "Home"

    def build(self):
        ttk.Label(self.body, text="Home", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="Connection, account, services, devices and pending work.", style="Muted.TLabel").pack(
            anchor="w", pady=(4, 12)
        )
        row(self.body)
        button(self.body, "Refresh dashboard", self.refresh, accent=True)

        self.connection_text = tk.StringVar(value="Checking connection…")
        connection = Card(self.body, "CORE-HOST connection", self.colors)
        ttk.Label(connection.body, textvariable=self.connection_text, style="Surface.TLabel", wraplength=820).pack(anchor="w")

        self.account_text = tk.StringVar(value="No server account")
        account = Card(self.body, "Current account", self.colors)
        ttk.Label(account.body, textvariable=self.account_text, style="Surface.TLabel", wraplength=820).pack(anchor="w")

        self.device_text = tk.StringVar(value="")
        device = Card(self.body, "This device", self.colors)
        ttk.Label(device.body, textvariable=self.device_text, style="Surface.TLabel", wraplength=820).pack(anchor="w")

        self.services_text = tk.StringVar(value="Connect to CORE-HOST to load service health.")
        services = Card(self.body, "Services and health", self.colors)
        ttk.Label(services.body, textvariable=self.services_text, style="Surface.TLabel", wraplength=820).pack(anchor="w")

        self.tasks_text = tk.StringVar(value="")
        tasks = Card(self.body, "Work and synchronization", self.colors)
        ttk.Label(tasks.body, textvariable=self.tasks_text, style="Surface.TLabel", wraplength=820).pack(anchor="w")

        self.hardware_text = tk.StringVar(value="")
        hardware = Card(self.body, "Local hardware", self.colors)
        ttk.Label(hardware.body, textvariable=self.hardware_text, style="Surface.TLabel", wraplength=820).pack(anchor="w")

        self.notice_text = tk.StringVar(value="No announcements.")
        notice = Card(self.body, "Notifications and announcements", self.colors)
        ttk.Label(notice.body, textvariable=self.notice_text, style="Surface.TLabel", wraplength=820).pack(anchor="w")
        self.refresh()

    def refresh(self):
        manager = self.app.connection
        profile = manager.profile or {}
        self.connection_text.set(
            f"State: {manager.state.label}\nServer: {manager.server or 'not configured'}\n"
            f"TLS verification: enabled by default\nEvent channel: enabled when the server supports tickets"
        )
        self.account_text.set(
            f"Name: {profile.get('name', '—')}\nUsername: {profile.get('username', '—')}\n"
            f"Role: {profile.get('role', self.app.binding_role())}"
        )
        self.device_text.set(
            f"Application: {APP_NAME} {__version__}\nPlatform: {platform.system()} {platform.release()} ({platform.machine()})\n"
            f"Hostname: {socket.gethostname()}\nLocal profile: {self.app.profile_name()}"
        )
        self.tasks_text.set(
            f"Pending local records: {self.app.pending_record_count()}\n"
            f"Pending uploads: {self.app.pending_file_count()}\n"
            f"Local history entries: {self.app.local_history_count()}\n"
            f"Automatic sync: {'on' if self.app.preferences.get('auto_sync', True) else 'off'}"
        )
        self.hardware_text.set(json.dumps(self.app.hardware_report(), indent=2))
        self._load_services()

    def _load_services(self):
        if self.app.connection.state.name != "ONLINE":
            self.services_text.set("Offline. Service health is available when CORE-HOST is reachable.")
            return

        def work():
            api = self.app.require_remote()
            services = api.request("GET", "/api/services")
            try:
                ai = api.request("GET", "/api/ai-status")
            except Exception:
                ai = {}
            return services, ai

        def done(result):
            services, ai = result
            lines = []
            for item in services.get("services", []):
                status = "enabled" if item.get("enabled", True) else "disabled"
                lines.append(
                    f"• {item.get('name', item.get('id'))}: {status} · "
                    f"installed source: {item.get('installed_source', False)} · {item.get('execution_mode', item.get('adapter', ''))}"
                )
            if ai:
                lines.append("AI: " + json.dumps(ai, sort_keys=True))
            self.services_text.set("\n".join(lines) or "No services reported.")

        self.app.run_async(work, done, lambda exc: self.services_text.set(f"Service status unavailable: {exc}"))
