from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from ..widgets import button, field, row
from .base import Page


class ConnectionPage(Page):
    title = "Connection"

    def build(self):
        ttk.Label(self.body, text="C.O.R.E. connection", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            self.body,
            text="RISARMS-UI connects to CORE-HOST over HTTPS/WSS. Local files and history remain usable offline.",
            style="Muted.TLabel", wraplength=820,
        ).pack(anchor="w", pady=(4, 12))

        card = tk.Frame(self.body, background=self.colors["surface"], highlightthickness=1,
                        highlightbackground=self.colors["border"])
        card.pack(fill="x", pady=(0, 12))
        form = tk.Frame(card, background=self.colors["surface"])
        form.pack(fill="x", padx=16, pady=14)

        self.server = field(form, "Server HTTPS origin", self.app.default_server())
        self.username = field(form, "Server email or username")
        self.password = field(form, "Server password", secret=True)
        self.display_name = field(form, "Display name (registration / first-owner setup)")
        self.setup_key = field(form, "First-owner setup key from the server journal", secret=True)

        buttons = row(form)
        button(buttons, "Connect and sign in", self.connect, accent=True)
        button(buttons, "Register and request access", self.register)
        button(buttons, "First-owner setup", self.first_owner)
        button(buttons, "Disconnect / work locally", self.disconnect)

        self.note = ttk.Label(form, text="A local profile binds to one server account. No password or session token is stored.",
                              style="SurfaceMuted.TLabel", wraplength=800)
        self.note.pack(anchor="w", pady=(10, 0))

        self.state = ttk.Label(self.body, text="", style="Heading.TLabel", wraplength=820)
        self.state.pack(anchor="w", pady=(4, 8))
        button(self.body, "Lock local profile", self.app.lock_profile)
        button(self.body, "Sign out of server", self.disconnect)

    def refresh(self):
        manager = self.app.connection
        self.state.configure(text=f"Server: {manager.state.label}" + (f" · {manager.server}" if manager.server else ""))

    def _values(self):
        return (
            self.server.get().strip(),
            self.username.get().strip(),
            self.password.get(),
            self.display_name.get().strip(),
            self.setup_key.get(),
        )

    def connect(self):
        server, username, password, _, _ = self._values()
        self.password.delete(0, "end")
        if not username or not password:
            messagebox.showerror("Connection", "Enter the server username and password.")
            return

        def work():
            return self.app.connection.connect(server, username, password)

        def done(profile):
            self.app.persist_server(server)
            self.app.on_connected(profile, password)
            self.refresh()

        self.app.run_async(work, done, self.app.show_error)

    def first_owner(self):
        server, username, password, name, setup_key = self._values()
        self.password.delete(0, "end")
        self.setup_key.delete(0, "end")
        if not all((server, username, password, name, setup_key)):
            messagebox.showerror("First-owner setup", "All first-owner fields are required.")
            return

        def work():
            api = self.app.connection.api_factory(server)
            try:
                api.setup_owner(setup_key, username, password, name)
            finally:
                api.close()
            return self.app.connection.connect(server, username, password)

        def done(profile):
            self.app.persist_server(server)
            self.app.on_connected(profile, password)
            self.refresh()

        self.app.run_async(work, done, self.app.show_error)

    def register(self):
        server, username, password, name, _ = self._values()
        self.password.delete(0, "end")
        if not all((server, username, password, name)):
            messagebox.showerror("Registration", "Server, username, password and display name are required.")
            return

        def work():
            api = self.app.connection.api_factory(server)
            try:
                return api.register(username, password, name)
            finally:
                api.close()

        def done(result):
            messagebox.showinfo("Access requested", result.get("message", "Your access request is pending owner approval."))
            self.app.persist_server(server)

        self.app.run_async(work, done, self.app.show_error)

    def disconnect(self):
        self.app.disconnect()


class PasswordChangeDialog:
    @staticmethod
    def ask(app, username, current_password):
        new = simpledialog.askstring("Change temporary password", "Choose a new server password (12+ characters):",
                                     show="*", parent=app.root)
        if not new:
            return
        confirm = simpledialog.askstring("Confirm new password", "Enter the new password again:",
                                         show="*", parent=app.root)
        if new != confirm or len(new) < 12:
            messagebox.showerror("Password", "Passwords must match and contain at least 12 characters.")
            return

        def work():
            app.require_remote().change_password(current_password, new)
            return True

        def done(_):
            app.disconnect()
            app.notify("Password changed", "The temporary password was replaced. Sign in again with the new password.")

        app.run_async(work, done, app.show_error)
