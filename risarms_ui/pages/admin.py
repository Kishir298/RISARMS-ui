from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from ..connection.states import ConnectionState
from ..owner_actions import summarize_action
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

        queue = ttk.LabelFrame(self.body, text="Queued owner approvals")
        queue.pack(fill="x", pady=(0, 10))
        self.queue_list = tk.Listbox(queue, width=1, height=4)
        self.queue_list.pack(fill="x", padx=10, pady=6)
        self.queue_list.bind("<<ListboxSelect>>", self.show_selected_action)
        ttk.Label(queue, text="Selected request details (review before deciding)", style="Muted.TLabel").pack(anchor="w", padx=10)
        self.queue_detail = tk.Text(queue, width=1, height=10, wrap="word", state="disabled")
        self.queue_detail.pack(fill="x", padx=10, pady=6)
        queue_actions = action_row(queue)
        for label, decision in (("Refresh queue", None), ("Approve selected", "approve"), ("Reject selected", "reject")):
            tk.Button(queue_actions, text=label, command=lambda decision=decision: self.decide(decision)).pack(side="left", padx=(0, 5))
        queue_actions.pack_configure(padx=10, pady=(0, 8))
        self.pending_actions = []
        self.set_text(self.queue_detail, "Select a queued request to review the full action and payload.")

        self.output = tk.Text(self.body, width=1, height=12, wrap="word", state="disabled")
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

    def load_queue(self):
        if self.app.connection.state is not ConnectionState.ONLINE:
            self.set_text(self.queue_detail, "Connect to CORE to review queued owner actions.")
            return

        def work():
            return self.app.require_remote().request("GET", "/api/owner-actions", None)

        def done(data):
            self.pending_actions = data.get("actions", [])
            self.queue_list.delete(0, "end")
            for item in self.pending_actions:
                self.queue_list.insert("end", f"{item.get('action')} · {item.get('requested_by', '')[:8]} · {item.get('id')[:8]}")
            if self.pending_actions:
                self.queue_list.selection_set(0)
                self.queue_list.activate(0)
                self.show_selected_action()
            else:
                self.set_text(self.queue_detail, "No queued owner actions. Select Refresh queue to check again.")

        self.app.run_async(work, done, lambda exc: self.set_text(self.queue_detail, f"Queue unavailable: {exc}"))

    def show_selected_action(self, _event=None):
        selected = self.queue_list.curselection()
        if not selected or selected[0] >= len(self.pending_actions):
            self.set_text(self.queue_detail, "Select a queued request to review the full action and payload.")
            return
        self.set_text(self.queue_detail, summarize_action(self.pending_actions[selected[0]]))

    def decide(self, decision):
        if decision is None:
            self.load_queue()
            return
        selected = self.queue_list.curselection()
        if not selected:
            messagebox.showerror("Owner actions", "Select a queued request first.")
            return
        try:
            item = self.pending_actions[selected[0]]
        except IndexError:
            messagebox.showerror("Owner actions", "The queue changed. Refresh and select the request again.")
            return
        summary = summarize_action(item)
        self.set_text(self.queue_detail, summary)
        password = self.reauth()
        if password is None:
            return
        if decision == "approve":
            confirmed = messagebox.askyesno(
                "Approve owner action?",
                f"{summary}\n\nApprove this request and apply these changes?",
                parent=self.app.root,
            )
        else:
            confirmed = messagebox.askyesno(
                "Reject owner action?",
                f"{summary}\n\nReject this request? The requested changes will not be applied.",
                parent=self.app.root,
            )
        if not confirmed:
            return

        def work():
            api = self.app.require_remote()
            api.request("POST", "/api/reauth", {"password": password})
            return api.request("POST", f"/api/owner-actions/{item['id']}/{decision}", None)

        def done(data):
            messagebox.showinfo("Owner action", f"Request is now {data.get('action', {}).get('status', 'decided')}.")
            self.load_queue()

        self.app.run_async(work, done, self.app.show_error)

    def refresh(self):
        self.load_queue()
