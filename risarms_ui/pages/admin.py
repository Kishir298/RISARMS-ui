from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from ..widgets import row as action_row
from .base import Page


class AdministrationPage(Page):
    title = "Administration"
    owner_only = True

    def build(self):
        ttk.Label(self.body, text="Administration", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="Owner-only account, device, service and audit management. CORE-HOST enforces every permission.",
                  style="Muted.TLabel", wraplength=820).pack(anchor="w", pady=(4, 12))

        accounts = ttk.LabelFrame(self.body, text="Accounts and access requests")
        accounts.pack(fill="x", pady=(0, 10))
        self.access_list = tk.Listbox(accounts, width=1, height=5)
        self.access_list.pack(fill="x", padx=10, pady=6)
        actions = action_row(accounts)
        for label, action in (("Refresh accounts", None), ("Approve", "approve"), ("Reject", "reject"), ("Revoke access", "revoke")):
            tk.Button(actions, text=label, command=lambda action=action: self.review(action)).pack(side="left", padx=(0, 5))
        actions.pack_configure(padx=10, pady=(0, 8))

        jobs = ttk.LabelFrame(self.body, text="Owner actions")
        jobs.pack(fill="x", pady=(0, 10))
        self.action = ttk.Combobox(jobs, state="readonly", width=28,
                                    values=("Profiles", "Create member", "Settings", "Save settings", "Audit events",
                                            "Server status", "Devices", "Jobs", "Quotas"))
        self.action.set("Profiles")
        self.action.pack(anchor="w", padx=10, pady=6)
        self.body_text = tk.Text(jobs, width=1, height=6)
        self.body_text.insert("1.0", "{}")
        self.body_text.pack(fill="x", padx=10, pady=6)
        ttk.Button(jobs, text="Run owner action", command=self.run_action).pack(anchor="w", padx=10, pady=(0, 8))

        self.output = tk.Text(self.body, width=1, height=16, wrap="word", state="disabled")
        self.output.pack(fill="both", expand=True)
        self.members = []

    def reauth(self):
        return simpledialog.askstring("Unlock administration", "Re-enter your owner password:", show="*", parent=self.app.root)

    def request(self, password, method, path, body=None):
        api = self.app.require_remote()
        api.request("POST", "/api/reauth", {"password": password})
        return api.request(method, path, body)

    def review(self, action):
        password = self.reauth()
        if password is None:
            return
        if action is None:
            def done(data):
                self.members = data.get("profiles", data if isinstance(data, list) else [])
                self.access_list.delete(0, "end")
                for row in self.members:
                    self.access_list.insert("end", f"{row.get('name')} · {row.get('username')} · {row.get('role')}")
            self.app.run_async(lambda: self.request(password, "GET", "/api/profiles"), done, self.app.show_error)
            return
        selected = self.access_list.curselection()
        if not selected:
            messagebox.showerror("Access", "Select an account first.")
            return
        identity = self.members[selected[0]]["id"]

        def work():
            return self.request(password, "POST", "/api/access", {"user_id": identity, "action": action})

        def done(data):
            messagebox.showinfo("Access updated", f"{data.get('username', identity)} is now {data.get('role', 'updated')}.")
            self.review(None)

        self.app.run_async(work, done, self.app.show_error)

    def run_action(self):
        mapping = {
            "Profiles": ("GET", "/api/profiles"),
            "Create member": ("POST", "/api/profiles"),
            "Settings": ("GET", "/api/settings"),
            "Save settings": ("POST", "/api/settings"),
            "Audit events": ("GET", "/api/audit"),
            "Server status": ("GET", "/api/status"),
            "Devices": ("GET", "/api/devices"),
            "Jobs": ("GET", "/api/jobs"),
            "Quotas": ("GET", "/api/quotas"),
        }
        method, path = mapping[self.action.get()]
        try:
            body = json.loads(self.body_text.get("1.0", "end"))
        except ValueError:
            messagebox.showerror("JSON", "Enter a valid JSON object.")
            return
        password = self.reauth()
        if password is None:
            return
        self.app.run_async(lambda: self.request(password, method, path, body),
                           lambda data: self.set_text(self.output, json.dumps(data, indent=2)), self.app.show_error)

    def refresh(self):
        pass
