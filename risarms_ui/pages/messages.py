"""Human-to-human messaging between approved CORE accounts.

This is distinct from ASIS/ASCS/TIVISS assistant chat. Queued messages stay
queued until the server acknowledges them; a queued message is never shown as
delivered.
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from ..widgets import button, field
from ..widgets import row as action_row
from .base import Page

STATUS_LABELS = {
    "queued": "Queued (not delivered)",
    "sent": "Sent",
    "delivered": "Delivered",
    "read": "Read",
    "failed": "Failed",
}


class MessagesPage(Page):
    title = "Messages"
    owner_only = False

    def build(self):
        ttk.Label(self.body, text="Messages", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            self.body,
            text="Direct messages between approved CORE accounts. Assistant chat lives in the ASIS page; "
                 "messaging never enables tools or coding access.",
            style="Muted.TLabel", wraplength=820,
        ).pack(anchor="w", pady=(4, 12))

        self.peer = field(self.body, "Recipient username or account id")
        actions = action_row(self.body)
        button(actions, "Open conversation", self.open_conversation, accent=True)
        button(actions, "Refresh threads", self.refresh_threads)
        button(actions, "Send queued", self.send_queued)

        ttk.Label(self.body, text="Conversations", style="Subheading.TLabel").pack(anchor="w", pady=(16, 4))
        self.thread_list = tk.Listbox(self.body, width=1, height=5)
        self.thread_list.pack(fill="x")
        self.thread_list.bind("<<ListboxSelect>>", self.select_thread)

        ttk.Label(self.body, text="Conversation", style="Subheading.TLabel").pack(anchor="w", pady=(16, 4))
        self.transcript = tk.Text(self.body, width=1, height=14, wrap="word", state="disabled")
        self.transcript.pack(fill="both", expand=True)

        ttk.Label(self.body, text="Message", style="Surface.TLabel").pack(anchor="w", pady=(10, 3))
        self.compose = tk.Text(self.body, width=1, height=4, wrap="word")
        self.compose.pack(fill="x")
        compose_actions = action_row(self.body)
        button(compose_actions, "Send", self.send, accent=True)
        button(compose_actions, "Mark read", self.mark_read)
        self.note = tk.StringVar(value="")
        ttk.Label(self.body, textvariable=self.note, style="Muted.TLabel", wraplength=820).pack(anchor="w", pady=8)
        self.refresh()

    # ------------------------------------------------------------------ state
    def current_peer(self) -> str:
        return self.peer.get().strip()

    def refresh(self):
        pending = self.app.pending_message_count()
        suffix = f" · {pending} queued for the next sync" if pending else ""
        self.note.set(
            ("Online" if self.app.connection.state.connected else "Offline")
            + " · messages are stored per local profile" + suffix
        )
        self.render_transcript()
        self.refresh_threads(quiet=True)

    def render_transcript(self):
        peer = self.current_peer()
        lines = []
        if peer:
            for row in self.app.store.messages(self.app.profile, peer):
                who = "You" if row["direction"] == "out" else peer
                stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["created"]))
                state = STATUS_LABELS.get(row["status"], row["status"])
                detail = f" [{state}]" if row["direction"] == "out" else ""
                error = f" ({row['error']})" if row["error"] else ""
                lines.append(f"{stamp} · {who}{detail}{error}\n{row['body']}\n")
        self.set_text(self.transcript, "\n".join(lines) if lines else "Open a conversation to read messages.")

    def refresh_threads(self, quiet: bool = False):
        rows = self.app.store.threads(self.app.profile)
        self.thread_rows = rows
        self.thread_list.delete(0, "end")
        for row in rows:
            label = row.get("name") or row.get("username") or row["peer"]
            unread = f" · {row['unread']} unread" if row["unread"] else ""
            self.thread_list.insert("end", f"{label}{unread} — {row['last_body']}")

    def select_thread(self, event=None):
        selection = self.thread_list.curselection()
        if not selection or not getattr(self, "thread_rows", None):
            return
        row = self.thread_rows[selection[0]]
        self.peer.delete(0, "end")
        self.peer.insert(0, row["peer"])
        self.render_transcript()

    def open_conversation(self):
        peer = self.current_peer()
        if not peer:
            messagebox.showinfo("Messages", "Enter a recipient username or account id.")
            return
        self.render_transcript()
        if not self.app.connection.state.connected:
            self.note.set("Offline · showing messages cached on this device.")
            return

        def work():
            return self.app.require_remote().request("GET", f"/api/messages?peer={peer}")

        def done(result):
            rows = result.get("messages", []) if isinstance(result, dict) else []
            self.app.store.record_incoming(self.app.profile, peer, rows)
            self.render_transcript()
            self.note.set(f"Loaded {len(rows)} message(s)")

        self.app.run_async(work, done, self.app.show_error)

    def send(self):
        peer = self.current_peer()
        body = self.compose.get("1.0", "end").strip()
        try:
            self.app.store.queue_message(self.app.profile, peer, body)
        except Exception as exc:
            messagebox.showerror("Message", str(exc))
            return
        self.compose.delete("1.0", "end")
        self.render_transcript()
        self.note.set("Queued locally. It is sent on the next sync and only then marked delivered.")
        if self.app.connection.state.connected:
            self.send_queued()

    def send_queued(self):
        if not self.app.connection.state.connected:
            self.note.set("Offline · queued messages will be sent automatically after reconnect.")
            return
        if not self.app.pending_message_count():
            self.note.set("Nothing queued.")
            return

        def work():
            return self.app.store.flush_messages(self.app.profile, self.app.require_remote())

        def done(sent):
            self.render_transcript()
            remaining = self.app.pending_message_count()
            self.note.set(f"Sent {sent} message(s)" + (f" · {remaining} still queued" if remaining else ""))

        self.app.run_async(work, done, self.app.show_error)

    def mark_read(self):
        peer = self.current_peer()
        if not peer:
            return
        self.app.store.mark_thread_read(self.app.profile, peer)
        self.render_transcript()
        self.refresh_threads(quiet=True)
