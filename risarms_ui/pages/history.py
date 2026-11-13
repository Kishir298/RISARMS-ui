from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk

from ..widgets import button, row
from .base import Page


class HistoryPage(Page):
    title = "History"

    def build(self):
        ttk.Label(self.body, text="History", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="Local task history and account-scoped server conversations.", style="Muted.TLabel").pack(
            anchor="w", pady=(4, 12)
        )
        actions = row(self.body)
        button(actions, "Refresh local history", self.refresh, accent=True)
        button(actions, "Load server chat history", self.server_history)
        self.text = tk.Text(self.body, width=1, height=24, wrap="word", state="disabled")
        self.text.pack(fill="both", expand=True, pady=8)
        self.refresh()

    def refresh(self):
        rows = self.app.store.history(self.app.profile)
        if not rows:
            self.set_text(self.text, "No completed local task history yet.")
            return
        self.set_text(self.text, "\n\n".join(
            f"[{row.get('source', '')}] {row.get('prompt', '')}\n{row.get('output', '')}" for row in rows
        ))

    def server_history(self):
        def work():
            return self.app.require_remote().request("GET", "/api/chats")

        self.app.run_async(work, lambda data: self.set_text(self.text, json.dumps(data, indent=2)), self.app.show_error)
