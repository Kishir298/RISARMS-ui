from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from ..jobs import JobSession
from ..widgets import button, field, row
from .base import Page


class TasksPage(Page):
    title = "Tasks"

    def build(self):
        ttk.Label(self.body, text="Tasks", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="Run work on CORE-HOST or an installed local service. Coding requires confirmation.",
                  style="Muted.TLabel", wraplength=820).pack(anchor="w", pady=(4, 12))

        controls = row(self.body)
        self.source = ttk.Combobox(controls, state="readonly", values=("Auto", "Server", "Local model", "Local service"),
                                   width=16)
        self.source.set("Auto")
        self.source.pack(side="left", padx=(0, 8))
        self.service = ttk.Combobox(controls, state="readonly", values=("asis", "ascs", "tiviss"), width=12)
        self.service.set("asis")
        self.service.pack(side="left", padx=(0, 8))
        self.mode = ttk.Combobox(controls, state="readonly", values=("plan", "build", "auto"), width=10)
        self.mode.set("plan")
        self.mode.pack(side="left")

        self.model_name = field(self.body, "Installed local Ollama model", self.app.preferences.get("model", ""))
        ttk.Label(self.body, text="Prompt", style="Surface.TLabel").pack(anchor="w", pady=(8, 3))
        self.prompt = tk.Text(self.body, width=1, height=4, wrap="word")
        self.prompt.pack(fill="x")

        actions = row(self.body)
        button(actions, "Run task", self.send, accent=True)
        button(actions, "Stop", self.stop)
        button(actions, "Save output locally", self.save_output)
        button(actions, "Refresh service availability", self.services)

        self.status = tk.StringVar(value="Idle")
        ttk.Label(self.body, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        ttk.Label(self.body, text="Output", style="Surface.TLabel").pack(anchor="w", pady=(8, 3))
        self.output = tk.Text(self.body, width=1, height=18, wrap="word", state="disabled")
        self.output.pack(fill="both", expand=True)
        self.session = None
        self.output_text = ""

    def _append(self, text):
        self.output_text = (self.output_text + text)[-500000:]
        self.app.post_ui("append", (self, text))

    def _append_ui(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text)
        if self.output.count("1.0", "end", "chars")[0] > 500000:
            self.output.delete("1.0", "1.0+50000c")
        self.output.see("end")
        self.output.configure(state="disabled")

    def append_output(self, text):
        self._append_ui(text)

    def job_complete(self, result):
        self.complete(result)

    def job_failed(self, exc):
        self.fail(exc)

    def send(self):
        if self.session and self.session.running:
            messagebox.showinfo("Task in progress", "Wait for the current task or press Stop.")
            return
        prompt = self.prompt.get("1.0", "end").strip()
        if not prompt or len(prompt) > 32000:
            messagebox.showerror("Task", "Enter 1–32000 characters.")
            return
        source = self.source.get()
        service = self.service.get()
        mode = self.mode.get()
        if source == "Auto":
            source = "Server" if self.app.connection.state.name == "ONLINE" else "Local model"
        if service == "ascs" and source in ("Server", "Local service"):
            if not messagebox.askyesno("Allow coding task", "ASCS can generate and modify files. Continue?"):
                return
        if service in ("ascs", "tiviss") and self.app.connection.profile and self.app.connection.profile.get("role") != "owner":
            messagebox.showerror("Permission", "ASCS and TIVISS require the CORE owner account.")
            return
        self.output_text = ""
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        self.status.set("Starting…")
        self.session = JobSession(self.app, self._append, self._done, self._error)
        if source == "Server":
            self.session.start_server(service, prompt, mode, persist=self.app.preferences.get("chat_storage", "both") != "local")
        elif source == "Local service":
            root = self.app.preferences.get(service, "")
            home = self.app.store.directory / "profiles" / self.app.profile
            self.session.start_local_service(service, root, str(home), prompt, self.model_name.get().strip(), mode)
        else:
            history = []
            previous = [row for row in self.app.store.history(self.app.profile)
                        if row["source"] == "Local model:" + service][:3]
            for row in reversed(previous):
                history.extend([
                    {"role": "user", "content": row["prompt"][-6000:]},
                    {"role": "assistant", "content": row["output"][-6000:]},
                ])
            self.session.start_local_model(self.model_name.get().strip(), prompt, history)

    def _done(self, result):
        self.app.post_ui("job_done", (self, result))

    def _error(self, exc):
        self.app.post_ui("job_error", (self, exc))

    def complete(self, result):
        self.status.set(f"Finished: {result}")
        self.app.store.log(self.app.profile, self.source.get() + ":" + self.service.get(),
                           self.prompt.get("1.0", "end").strip(), self.output_text)
        if self.source.get() == "Local model" and self.app.preferences.get("chat_storage", "both") in ("server", "both"):
            self.app.store.save(self.app.profile, self.service.get(), "chat-" + str(time.time_ns()),
                                {"output": self.output_text})
        self.app.refresh_current()

    def fail(self, exc):
        self.status.set(f"Failed: {exc}")
        messagebox.showerror("Task failed", str(exc))

    def stop(self):
        if self.session:
            self.session.stop()
            self.status.set("Cancellation requested")

    def save_output(self):
        if not self.output_text:
            return
        key = "task-" + str(time.time_ns())
        self.app.store.save(self.app.profile, self.service.get(), key, {"output": self.output_text})
        messagebox.showinfo("Saved locally", f"Saved as {key}. Use Sync to send it to RESCS.")

    def services(self):
        def work():
            return self.app.require_remote().request("GET", "/api/services")

        self.app.run_async(work, lambda data: self._append("\n" + str(data) + "\n"), self.app.show_error)

    def refresh(self):
        pass
