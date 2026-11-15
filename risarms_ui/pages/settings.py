from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import __version__, updates
from ..widgets import button, field, row
from .base import Page


class SettingsPage(Page):
    title = "Settings"

    def build(self):
        ttk.Label(self.body, text="Settings", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="Persistent local preferences. Secrets are never stored here.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 12))

        general = ttk.LabelFrame(self.body, text="General")
        general.pack(fill="x", pady=(0, 10))
        row_general = tk.Frame(general, background=self.colors["bg"])
        row_general.pack(fill="x", padx=10, pady=8)
        ttk.Label(row_general, text="Theme").pack(side="left", padx=(0, 8))
        self.theme = ttk.Combobox(row_general, state="readonly", values=("light", "dark"), width=10)
        self.theme.set(self.app.preferences.get("theme", "light"))
        self.theme.pack(side="left")
        ttk.Button(row_general, text="Apply theme", command=self.apply_theme).pack(side="left", padx=8)
        self.auto_sync = tk.BooleanVar(value=bool(self.app.preferences.get("auto_sync", True)))
        ttk.Checkbutton(row_general, text="Automatic synchronization for server-designated data",
                        variable=self.auto_sync).pack(side="left", padx=12)
        ttk.Label(general, text=f"Application version: {__version__} · data: {self.app.data_directory}",
                  style="Muted.TLabel").pack(anchor="w", padx=10, pady=(0, 8))

        connection = ttk.LabelFrame(self.body, text="Connection")
        connection.pack(fill="x", pady=(0, 10))
        self.server = field(connection, "CORE-HOST origin", self.app.default_server())
        ttk.Label(connection, text="TLS verification is enabled by default. Custom certificate trust must be configured at the OS level for the Caddy internal CA.",
                  style="Muted.TLabel", wraplength=820).pack(anchor="w", padx=10)
        self.ca_file = field(connection, "Optional CA bundle path (advisory)", self.app.preferences.get("ca_file", ""))
        ttk.Label(connection, text="Credential storage: " + ("OS keychain available" if self.app.credentials_persistent() else "memory only"),
                  style="Muted.TLabel").pack(anchor="w", padx=10, pady=4)

        services = ttk.LabelFrame(self.body, text="Local services and AI")
        services.pack(fill="x", pady=(0, 10))
        self.paths = {}
        for name in ("asis", "ascs", "tiviss"):
            self.paths[name] = field(services, name.upper() + " source directory with its own .venv",
                                     self.app.preferences.get(name, ""))
        self.model = field(services, "Installed local Ollama model", self.app.preferences.get("model", ""))

        storage = ttk.LabelFrame(self.body, text="Storage")
        storage.pack(fill="x", pady=(0, 10))
        row_storage = tk.Frame(storage, background=self.colors["bg"])
        row_storage.pack(fill="x", padx=10, pady=8)
        ttk.Label(row_storage, text="Chat storage").pack(side="left", padx=(0, 8))
        self.chat_storage = ttk.Combobox(row_storage, state="readonly", values=("both", "local", "server"), width=10)
        self.chat_storage.set(self.app.preferences.get("chat_storage", "both"))
        self.chat_storage.pack(side="left")
        ttk.Label(storage, text="Local-only data is never uploaded automatically. Pending records require explicit Sync.",
                  style="Muted.TLabel").pack(anchor="w", padx=10, pady=(0, 8))

        update_frame = ttk.LabelFrame(self.body, text="Updates")
        update_frame.pack(fill="x", pady=(0, 10))
        self.update_url = field(update_frame, "Signed update manifest URL",
                                self.app.preferences.get("update_url", "https://github.com/Kishir298/RISARMS-ui/releases/latest/download/update.json"))
        self.update_key = field(update_frame, "Ed25519 public key (base64)",
                                self.app.preferences.get("update_key", ""))
        row_update = tk.Frame(update_frame, background=self.colors["bg"])
        row_update.pack(fill="x", padx=10, pady=8)
        ttk.Label(row_update, text="Channel").pack(side="left", padx=(0, 8))
        self.update_channel = ttk.Combobox(row_update, state="readonly", values=("stable", "beta"), width=10)
        self.update_channel.set(self.app.preferences.get("update_channel", "stable"))
        self.update_channel.pack(side="left")
        ttk.Button(row_update, text="Check for updates", command=self.check_updates).pack(side="left", padx=8)
        ttk.Label(update_frame, text="Updates are checked and verified only; installation is never automatic.",
                  style="Muted.TLabel").pack(anchor="w", padx=10, pady=(0, 8))

        security = ttk.LabelFrame(self.body, text="Security")
        security.pack(fill="x", pady=(0, 10))
        ttk.Label(security, text="The local profile passphrase is an application lock, not disk encryption. Use full-disk encryption for local data.",
                  style="Muted.TLabel", wraplength=820).pack(anchor="w", padx=10, pady=8)

        actions = row(self.body)
        button(actions, "Save settings", self.save, accent=True)
        button(actions, "Lock local profile", self.app.lock_profile)
        button(actions, "Sign out of server", self.app.disconnect)

    def apply_theme(self):
        self.app.apply_theme(self.theme.get())
        self.app.save_preferences()

    def check_updates(self):
        url = self.update_url.get().strip()
        key = self.update_key.get().strip()
        channel = self.update_channel.get()
        if not key:
            messagebox.showerror("Updates", "Configure the Ed25519 public key first.")
            return

        def work():
            found = updates.fetch_manifest(url, key, channel=channel)
            return found, updates.update_available(__version__, found)

        def done(result):
            found, available = result
            messagebox.showinfo(
                "Updates",
                f"Latest {found.channel} version: {found.version}\n"
                + ("An update is available." if available else "This installation is current."),
            )

        self.app.run_async(work, done, self.app.show_error)

    def save(self):
        self.app.preferences.update({
            "theme": self.theme.get(),
            "auto_sync": bool(self.auto_sync.get()),
            "ca_file": self.ca_file.get().strip(),
            "model": self.model.get().strip(),
            "chat_storage": self.chat_storage.get(),
            "update_url": self.update_url.get().strip(),
            "update_key": self.update_key.get().strip(),
            "update_channel": self.update_channel.get(),
        })
        for name, entry in self.paths.items():
            self.app.preferences[name] = entry.get().strip()
        self.app.persist_server(self.server.get().strip())
        self.app.save_preferences()
        messagebox.showinfo("Settings", "Settings saved on this device.")
