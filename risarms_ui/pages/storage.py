from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlencode

from ..remote import sync_records
from ..widgets import button, field
from ..widgets import row as action_row
from .base import Page


class StoragePage(Page):
    title = "My Storage"

    def build(self):
        ttk.Label(self.body, text="My Storage", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="JSON records stored locally and synchronized to your own RESCS namespace.",
                  style="Muted.TLabel", wraplength=820).pack(anchor="w", pady=(4, 12))
        self.service = ttk.Combobox(self.body, state="readonly",
                                    values=self.app.record_services())
        self.service.set("workspace")
        self.service.pack(anchor="w")
        self.service.bind("<<ComboboxSelected>>", lambda event: self.refresh())

        self.record_list = tk.Listbox(self.body, width=1, height=7)
        self.record_list.pack(fill="x", pady=6)
        self.record_list.bind("<<ListboxSelect>>", self.select_record)
        self.key = field(self.body, "Record key")
        ttk.Label(self.body, text="JSON value", style="Surface.TLabel").pack(anchor="w", pady=(8, 3))
        self.value = tk.Text(self.body, width=1, height=8, wrap="word")
        self.value.insert("1.0", '{"text": ""}')
        self.value.pack(fill="both", expand=True)
        actions = action_row(self.body)
        button(actions, "Save locally / queue sync", self.save, accent=True)
        button(actions, "Refresh server cache", self.refresh_server)
        button(actions, "Sync pending records", self.sync)
        self.note = tk.StringVar(value="Pending records remain local until Sync.")
        ttk.Label(self.body, textvariable=self.note, style="Muted.TLabel").pack(anchor="w", pady=8)
        self.refresh()

    def refresh(self):
        services = self.app.record_services()
        self.service.configure(values=services)
        if self.service.get() not in services:
            self.service.set(services[0])
        rows = self.app.store.records(self.app.profile, self.service.get())
        self.visible = rows
        self.record_list.delete(0, "end")
        for row in rows:
            self.record_list.insert("end", row["key"] + ("  [pending sync]" if row["pending"] else "  [cached]"))
        pending = self.app.pending_record_count()
        self.note.set(f"{pending} pending local records · server deletions are not mirrored")

    def select_record(self, event=None):
        selection = self.record_list.curselection()
        if not selection:
            return
        row = self.visible[selection[0]]
        self.key.delete(0, "end")
        self.key.insert(0, row["key"])
        self.value.delete("1.0", "end")
        self.value.insert("1.0", json.dumps(row["value"], indent=2))

    def save(self):
        try:
            self.app.store.save(self.app.profile, self.service.get(), self.key.get(),
                                json.loads(self.value.get("1.0", "end")))
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Record", str(exc))

    def refresh_server(self):
        service = self.service.get()

        def work():
            remote = self.app.require_remote()
            offset = 0
            while offset < 100000:
                page = remote.request("GET", "/api/records?" + urlencode({"service": service, "limit": 100, "offset": offset}))
                if not isinstance(page.get("items"), list):
                    raise ValueError("Invalid record collection.")
                self.app.store.cache(self.app.profile, service, page["items"])
                offset += len(page["items"])
                if offset >= page.get("total", offset):
                    return offset
                if not page["items"]:
                    raise ValueError("Incomplete server record page.")
            raise ValueError("Record cache scan limit reached.")

        self.app.run_async(work, lambda count: (self.refresh(), self.note.set(f"Cached {count} records")),
                           self.app.show_error)

    def sync(self):
        def work():
            return sync_records(self.app.store, self.app.profile, self.app.require_remote())

        def done(result):
            self.refresh()
            conflicts = ", ".join(result["conflicts"]) or "none"
            messagebox.showinfo("Sync result", f"Saved: {result['saved']}\nConflicts kept locally: {conflicts}")

        self.app.run_async(work, done, self.app.show_error)
