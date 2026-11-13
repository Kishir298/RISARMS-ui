from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..widgets import Card, button
from .base import Page


class RescsPage(Page):
    title = "RESCS"

    def build(self):
        ttk.Label(self.body, text="R.E.S.C.S.", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="Account-scoped records and cloud files. Storage ownership is enforced by CORE-HOST.",
                  style="Muted.TLabel", wraplength=820).pack(anchor="w", pady=(4, 12))
        actions = tk.Frame(self.body, background=self.colors["bg"])
        actions.pack(fill="x", pady=(0, 10))
        button(actions, "Open My Storage", lambda: self.app.show_page("storage"), accent=True)
        button(actions, "Open Cloud Files", lambda: self.app.show_page("files"))
        button(actions, "Refresh status", self.refresh)

        status = Card(self.body, "RESCS status", self.colors)
        self.status_text = tk.StringVar(value="Connect to CORE-HOST to query storage status.")
        ttk.Label(status.body, textvariable=self.status_text, style="Surface.TLabel", wraplength=820).pack(anchor="w")

        limits = Card(self.body, "Documented limits", self.colors)
        ttk.Label(limits.body, text="8 MiB per file · 250 MiB server storage per account · 250 MiB local cache · SHA-256 integrity checks",
                  style="Surface.TLabel", wraplength=820).pack(anchor="w")
        self.refresh()

    def refresh(self):
        if self.app.connection.state.name != "ONLINE":
            self.status_text.set("Offline. Cached records and files remain available on this device.")
            return

        def work():
            api = self.app.require_remote()
            try:
                health = api.request("GET", "/api/health")
            except Exception:
                health = {}
            try:
                files = api.request("GET", "/api/files?limit=1")
            except Exception:
                files = {}
            return health, files

        def done(result):
            health, files = result
            self.status_text.set(
                f"Gateway: {health.get('service', 'CORE')} · API {health.get('api', 1)} · "
                f"capabilities: {', '.join(health.get('capabilities', [])) or 'none advertised'}\n"
                f"Server files visible: {files.get('total', 'unknown')}"
            )

        self.app.run_async(work, done, lambda exc: self.status_text.set(f"RESCS status unavailable: {exc}"))
