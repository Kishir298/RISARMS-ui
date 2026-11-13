from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from ..jobs import JobSession
from ..widgets import button, field, row
from .base import Page


class ServiceChatPage(Page):
    def __init__(self, parent, app, service: str, title: str, allow_local: bool = True, coding: bool = False):
        self.service = service
        self.title = title
        self.allow_local = allow_local
        self.coding = coding
        super().__init__(parent, app)

    def build(self):
        ttk.Label(self.body, text=self.title, style="Title.TLabel").pack(anchor="w")
        self.availability = tk.StringVar(value="Checking availability…")
        ttk.Label(self.body, textvariable=self.availability, style="Muted.TLabel", wraplength=820).pack(anchor="w", pady=(4, 8))

        ttk.Label(self.body, text="Conversation", style="Subheading.TLabel").pack(anchor="w")
        self.chat = tk.Text(self.body, width=1, height=18, wrap="word", state="disabled")
        self.chat.pack(fill="both", expand=True, pady=6)

        self.model_name = field(self.body, "Installed local Ollama model", self.app.preferences.get("model", ""))
        ttk.Label(self.body, text="Message", style="Surface.TLabel").pack(anchor="w", pady=(8, 3))
        self.prompt = tk.Text(self.body, width=1, height=4, wrap="word")
        self.prompt.pack(fill="x")
        actions = row(self.body)
        sources = ("Auto", "Server", "Local model", "Local service") if self.allow_local else ("Auto", "Server")
        self.source = ttk.Combobox(actions, state="readonly", values=sources, width=15)
        self.source.set("Auto")
        self.source.pack(side="left", padx=(0, 8))
        if self.coding:
            self.mode = ttk.Combobox(actions, state="readonly", values=("plan", "build", "auto"), width=10)
            self.mode.set("plan")
            self.mode.pack(side="left", padx=(0, 8))
        button(actions, "Send", self.send, accent=True)
        button(actions, "Stop generation", self.stop)
        button(actions, "New conversation", self.new_conversation)
        button(actions, "Save locally", self.save_local)
        self.status = tk.StringVar(value="")
        ttk.Label(self.body, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=6)
        self.session = None
        self.transcript = ""
        self.refresh()

    def append_output(self, text: str):
        self.transcript = (self.transcript + text)[-500000:]
        self.chat.configure(state="normal")
        self.chat.insert("end", text)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def send(self):
        if self.session and self.session.running:
            messagebox.showinfo("In progress", "Wait for the current response or press Stop generation.")
            return
        prompt = self.prompt.get("1.0", "end").strip()
        if not prompt or len(prompt) > 32000:
            messagebox.showerror("Message", "Enter 1–32000 characters.")
            return
        profile = self.app.connection.profile or {}
        if self.service in ("ascs", "tiviss") and profile.get("role") != "owner":
            messagebox.showerror("Permission", f"{self.title} requires the CORE owner account.")
            return
        if self.coding and not messagebox.askyesno("Allow coding task", "ASCS can generate and modify files. Continue?"):
            return
        source = self.source.get()
        if source == "Auto":
            source = "Server" if self.app.connection.state.name == "ONLINE" else "Local model"
        self.append_output(f"\n\nYou: {prompt}\n\n{self.service.upper()}: ")
        self.status.set("Generating…")
        self.session = JobSession(self.app, self.append_output, self.job_complete, self.job_failed)
        mode = self.mode.get() if self.coding else "plan"
        if source == "Server":
            self.session.start_server(self.service, prompt, mode,
                                      persist=self.app.preferences.get("chat_storage", "both") != "local")
        elif source == "Local service":
            root = self.app.preferences.get(self.service, "")
            home = self.app.store.directory / "profiles" / self.app.profile
            self.session.start_local_service(self.service, root, str(home), prompt,
                                             self.model_name.get().strip(), mode)
        else:
            history = []
            previous = [row for row in self.app.store.history(self.app.profile)
                        if row["source"].endswith(":" + self.service)][:3]
            for row in reversed(previous):
                history.extend([
                    {"role": "user", "content": row["prompt"][-6000:]},
                    {"role": "assistant", "content": row["output"][-6000:]},
                ])
            self.session.start_local_model(self.model_name.get().strip(), prompt, history)

    def job_complete(self, result):
        self.status.set(f"Finished: {result}")
        self.append_output(f"\n\n[{result}]")
        self.app.store.log(self.app.profile, self.source.get() + ":" + self.service,
                           self.prompt.get("1.0", "end").strip(), self.transcript)
        if self.app.preferences.get("chat_storage", "both") in ("server", "both"):
            self.app.store.save(self.app.profile, "asis", "chat-" + str(time.time_ns()),
                                {"prompt": self.prompt.get("1.0", "end").strip(), "output": self.transcript})
        self.prompt.delete("1.0", "end")

    def job_failed(self, exc):
        self.status.set(f"Failed: {exc}")
        messagebox.showerror("Service error", str(exc))

    def stop(self):
        if self.session:
            self.session.stop()
            self.status.set("Cancellation requested")

    def new_conversation(self):
        self.transcript = ""
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self.status.set("")

    def save_local(self):
        if not self.transcript:
            return
        self.app.store.save(self.app.profile, self.service, "chat-" + str(time.time_ns()),
                            {"output": self.transcript})
        messagebox.showinfo("Saved", "Conversation saved locally.")

    def refresh(self):
        if self.app.connection.state.name != "ONLINE":
            self.availability.set("Offline. Local modes remain available if installed.")
            return

        def work():
            return self.app.require_remote().request("GET", "/api/services")

        def done(data):
            for item in data.get("services", []):
                if item.get("id") == self.service:
                    state = "available" if item.get("enabled", True) else "disabled"
                    mode = item.get("execution_mode", item.get("adapter", ""))
                    self.availability.set(f"{self.title}: {state}" + (f" · {mode}" if mode else ""))
                    return
            self.availability.set(f"{self.title}: not reported by this CORE-HOST.")

        self.app.run_async(work, done, lambda exc: self.availability.set(f"Availability unavailable: {exc}"))
