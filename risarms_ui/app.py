"""Desktop application shell: local profile, workspace, navigation and sync.

The Tk event loop is only touched from the main thread. Worker results are
queued and drained on a short timer so network and service processes never
block the interface.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import queue
import shutil
import socket
import threading
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import messagebox, ttk

from . import APP_NAME, __version__
from .connection.api import APIError, validate_origin
from .connection.manager import ConnectionManager
from .connection.states import ConnectionState
from .layout import initial_geometry
from .pages.admin import AdministrationPage
from .pages.connection import PasswordChangeDialog
from .pages.devices import DevicesPage
from .pages.files import FilesPage
from .pages.history import HistoryPage
from .pages.home import HomePage
from .pages.messages import MessagesPage
from .pages.rescs import RescsPage
from .pages.service_chat import ServiceChatPage
from .pages.settings import SettingsPage
from .pages.storage import StoragePage
from .pages.tasks import TasksPage
from .paths import config_directory, data_directory, preferences_file, server_url_file
from .remote import sync_files, sync_records
from .security.credentials import CredentialStore
from .store import LocalStore
from .theme import apply_theme, palette
from .widgets import StatusPill

_LOG = logging.getLogger(__name__)

NAVIGATION = (
    ("home", "Home", HomePage, False, ""),
    ("messages", "Messages", MessagesPage, False, "Direct messages between approved accounts"),
    ("asis", "ASIS", lambda parent, app: ServiceChatPage(parent, app, "asis", "ASIS Assistant"), False, "Chat and general assistance"),
    ("ascs", "ASCS", lambda parent, app: ServiceChatPage(parent, app, "ascs", "ASCS Coding", coding=True), False, "Coding agent — owner account"),
    ("tiviss", "TIVISS", lambda parent, app: ServiceChatPage(parent, app, "tiviss", "TIVISS Vision", coding=False), False, "Vision and infrastructure — owner account"),
    ("tasks", "Tasks", TasksPage, False, "Server and local task execution"),
    ("history", "History", HistoryPage, False, "Local conversation history"),
    ("storage", "My Storage", StoragePage, False, "RESCS records with explicit sync"),
    ("files", "Cloud Files", FilesPage, False, "RESCS cloud files"),
    ("devices", "Device Access", DevicesPage, False, "This device, credential and server access"),
    ("rescs", "RESCS", RescsPage, False, "Storage service status"),
    ("settings", "Settings", SettingsPage, False, "Appearance, services and storage"),
    ("admin", "Administration", AdministrationPage, True, "Owner-only management"),
)

DEFAULT_PREFERENCES = {
    "theme": "light",
    "auto_sync": True,
    "chat_storage": "both",
    "server": "https://core.local",
    "local_profile": "",
    "model": "",
    "ca_file": "",
    "geometry": "",
    "asis": "",
    "ascs": "",
    "tiviss": "",
}


def setup_logging():
    directory = config_directory() / "logs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = RotatingFileHandler(directory / "risarms-ui.log", maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(item, RotatingFileHandler) for item in root.handlers):
        root.addHandler(handler)


def acquire_instance_lock():
    path = config_directory() / "instance.lock"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write("risarms-ui")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


class App:
    def __init__(self, enable_events: bool = True):
        self._lock = acquire_instance_lock()
        if self._lock is None:
            raise RuntimeError("RISARMS Portal is already running on this device.")
        self.closing = False
        self._sync_running = False
        self.config_directory = config_directory()
        self.config_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_directory = data_directory()
        self.preferences = self._load_preferences()
        self.credentials = CredentialStore()
        self.connection = ConnectionManager(
            credentials=self.credentials,
            on_state=self._state_changed,
            on_event=self._event_received,
            enable_events=enable_events,
        )
        self.ui_queue: queue.Queue = queue.Queue()
        self.profile = ""
        self.local_name = ""
        self.store = LocalStore(self.data_directory)
        self.pages: dict[str, object] = {}
        self.current = ""
        self.colors = palette(self.preferences.get("theme", "light"))
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self._tk_error
        self.status_var = tk.StringVar(value="Starting…")
        apply_theme(self.root, self.preferences.get("theme", "light"))
        self.show_unlock()
        self.root.after(60, self._drain)
        self.root.after(60000, self._auto_sync)

    # ------------------------------------------------------------------ state
    def _load_preferences(self) -> dict:
        data = dict(DEFAULT_PREFERENCES)
        try:
            loaded = json.loads(preferences_file().read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update({key: value for key, value in loaded.items() if key in DEFAULT_PREFERENCES})
        except (OSError, ValueError):
            pass
        return data

    def save_preferences(self):
        data = {key: self.preferences.get(key, value) for key, value in DEFAULT_PREFERENCES.items()}
        temporary = preferences_file().with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(preferences_file())

    def credentials_persistent(self) -> bool:
        return self.credentials.available()

    def data_path(self) -> Path:
        return self.data_directory

    def default_server(self) -> str:
        override = os.environ.get("RISARMS_UI_PORTAL_URL")
        if override:
            return override
        binding = self.store.binding(self.profile) if self.profile else None
        if binding and binding.get("server"):
            return binding["server"]
        if self.preferences.get("server"):
            return self.preferences["server"]
        try:
            return server_url_file().read_text(encoding="utf-8").strip()
        except OSError:
            return DEFAULT_PREFERENCES["server"]

    def persist_server(self, server: str):
        try:
            normalized = validate_origin(server)
        except ValueError:
            return
        self.preferences["server"] = normalized
        self.save_preferences()
        try:
            server_url_file().write_text(normalized + "\n", encoding="utf-8")
            server_url_file().chmod(0o600)
        except OSError:
            pass

    # ------------------------------------------------------------- local login
    def show_unlock(self):
        self._clear_root()
        self.root.resizable(False, False)
        width, height = 560, 500
        screen_width, screen_height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{width}x{height}+{max(0, (screen_width - width) // 2)}+{max(0, (screen_height - height) // 3)}")
        self.colors = apply_theme(self.root, self.preferences.get("theme", "light"))
        frame = tk.Frame(self.root, background=self.colors["bg"])
        frame.pack(fill="both", expand=True, padx=36, pady=28)
        ttk.Label(frame, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="A local profile unlocks this device. It is separate from your CORE-HOST account and never sent to the server.",
                  style="Muted.TLabel", wraplength=480).pack(anchor="w", pady=(6, 16))

        ttk.Label(frame, text="Local profile name", style="Surface.TLabel").pack(anchor="w", pady=(6, 3))
        self.unlock_name = ttk.Entry(frame)
        self.unlock_name.pack(fill="x")
        names = [row["name"] for row in self.store.profiles()]
        remembered = self.preferences.get("local_profile", "")
        if remembered in names:
            self.unlock_name.insert(0, remembered)
        elif names:
            self.unlock_name.insert(0, names[0])

        ttk.Label(frame, text="Local passphrase", style="Surface.TLabel").pack(anchor="w", pady=(12, 3))
        self.unlock_password = ttk.Entry(frame, show="*")
        self.unlock_password.pack(fill="x")
        self.unlock_password.bind("<Return>", lambda event: self.unlock())
        ttk.Label(frame, text="At least 12 characters. This is an application lock, not disk encryption.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 12))

        buttons = tk.Frame(frame, background=self.colors["bg"])
        buttons.pack(fill="x", pady=(8, 8))
        ttk.Button(buttons, text="Unlock", style="Accent.TButton", command=self.unlock).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Create local profile", command=self.create_profile).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Quit", command=self.close).pack(side="left")
        self.unlock_status = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.unlock_status, style="Muted.TLabel", wraplength=480).pack(anchor="w", pady=8)
        if not names:
            self.unlock_status.set("No local profile yet. Create one to begin.")
        self.unlock_name.focus_set()
        self.root.deiconify()
        self.root.lift()

    def unlock(self):
        name, password = self.unlock_name.get().strip(), self.unlock_password.get()
        self.unlock_password.delete(0, "end")
        try:
            identity = self.store.unlock(name, password)
        except PermissionError as exc:
            self.unlock_status.set(str(exc))
            return
        self.profile = identity
        self.local_name = name.lower()
        self.preferences["local_profile"] = self.local_name
        self.save_preferences()
        self.open_workspace()

    def create_profile(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Create local profile")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = tk.Frame(dialog, background=self.colors["bg"], padx=24, pady=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Create a local profile", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Use a name and passphrase you control. There is no recovery service.",
                  style="Muted.TLabel", wraplength=360).pack(anchor="w", pady=(4, 12))
        ttk.Label(frame, text="Name", style="Surface.TLabel").pack(anchor="w", pady=(4, 3))
        name = ttk.Entry(frame)
        name.pack(fill="x")
        ttk.Label(frame, text="Passphrase", style="Surface.TLabel").pack(anchor="w", pady=(10, 3))
        password = ttk.Entry(frame, show="*")
        password.pack(fill="x")
        ttk.Label(frame, text="Confirm passphrase", style="Surface.TLabel").pack(anchor="w", pady=(10, 3))
        confirm = ttk.Entry(frame, show="*")
        confirm.pack(fill="x")
        status = tk.StringVar(value="")
        ttk.Label(frame, textvariable=status, style="Muted.TLabel", wraplength=360).pack(anchor="w", pady=8)

        def create():
            if password.get() != confirm.get():
                status.set("Passphrases do not match.")
                return
            try:
                identity = self.store.create(name.get().strip(), password.get())
            except ValueError as exc:
                status.set(str(exc))
                return
            self.profile, self.local_name = identity, name.get().strip().lower()
            self.preferences["local_profile"] = self.local_name
            self.save_preferences()
            dialog.destroy()
            self.open_workspace()

        actions = tk.Frame(frame, background=self.colors["bg"])
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Create", style="Accent.TButton", command=create).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(side="left")
        name.focus_set()
        self.root.wait_window(dialog)

    # ------------------------------------------------------------------ workspace
    def open_workspace(self):
        self._clear_root()
        self.root.resizable(True, True)
        self.root.minsize(900, 600)
        geometry = self.preferences.get("geometry", "")
        if not geometry:
            width, height = initial_geometry(self.root.winfo_screenwidth(), self.root.winfo_screenheight())
            geometry = f"{width}x{height}"
        self.root.geometry(geometry)
        self.colors = apply_theme(self.root, self.preferences.get("theme", "light"))

        shell = tk.Frame(self.root, background=self.colors["bg"])
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, width=216, background=self.colors["sidebar"])
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, background=self.colors["sidebar"])
        brand.pack(fill="x", padx=18, pady=(20, 12))
        tk.Label(brand, text="RISARMS", background=self.colors["sidebar"], foreground="#ffffff",
                 font=("TkDefaultFont", 17, "bold")).pack(anchor="w")
        tk.Label(brand, text=f"Portal {__version__}", background=self.colors["sidebar"],
                 foreground=self.colors["sidebar_text"], font=("TkDefaultFont", 9)).pack(anchor="w")

        navigation = tk.Frame(sidebar, background=self.colors["sidebar"])
        navigation.pack(fill="both", expand=True, padx=10)
        self.nav_buttons = {}
        self.nav_owner_only = []
        for row_index, (key, label, _factory, owner_only, _subtitle) in enumerate(NAVIGATION):
            widget = tk.Button(
                navigation, text=label, anchor="w", bd=0, padx=12, pady=7,
                background=self.colors["sidebar"], foreground=self.colors["sidebar_text"],
                activebackground=self.colors["sidebar_active"], activeforeground="#ffffff",
                font=("TkDefaultFont", 10), command=lambda key=key: self.show_page(key),
            )
            widget.grid(row=row_index, column=0, sticky="ew", pady=1)
            self.nav_buttons[key] = widget
            if owner_only:
                self.nav_owner_only.append(key)
        navigation.grid_columnconfigure(0, weight=1)

        account = tk.Frame(sidebar, background=self.colors["sidebar"])
        account.pack(fill="x", padx=18, pady=14)
        self.sidebar_account = tk.StringVar(value=self.local_name or "Local profile")
        tk.Label(account, textvariable=self.sidebar_account, background=self.colors["sidebar"],
                 foreground=self.colors["sidebar_text"], anchor="w", justify="left", wraplength=180).pack(fill="x")
        tk.Button(account, text="Lock local profile", bd=0, anchor="w", padx=0, pady=4,
                  background=self.colors["sidebar"], foreground=self.colors["sidebar_text"],
                  activebackground=self.colors["sidebar"], activeforeground="#ffffff",
                  command=self.lock_profile).pack(fill="x", pady=(8, 0))

        main = tk.Frame(shell, background=self.colors["bg"])
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, background=self.colors["bg"])
        header.pack(fill="x", padx=22, pady=(16, 0))
        titles = tk.Frame(header, background=self.colors["bg"])
        titles.pack(side="left", fill="x", expand=True)
        self.page_title = tk.StringVar(value="Home")
        self.page_subtitle = tk.StringVar(value="")
        ttk.Label(titles, textvariable=self.page_title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, textvariable=self.page_subtitle, style="Muted.TLabel").pack(anchor="w")

        right = tk.Frame(header, background=self.colors["bg"])
        right.pack(side="right")
        self.pill = StatusPill(right, self.colors)
        self.pill.label.pack(side="left", padx=(0, 8))
        self.pill.set("Offline", "muted")
        self.account_label = tk.StringVar(value="No server account")
        ttk.Label(right, textvariable=self.account_label, style="Muted.TLabel").pack(side="left", padx=(0, 8))
        ttk.Button(right, text="Theme", command=self.toggle_theme).pack(side="left")

        self.content = tk.Frame(main, background=self.colors["bg"])
        self.content.pack(fill="both", expand=True, padx=22, pady=(8, 4))
        self.status_bar = ttk.Label(main, textvariable=self.status_var, style="Muted.TLabel", anchor="w")
        self.status_bar.pack(fill="x", padx=22, pady=(0, 10))
        self._update_role_visibility()
        self.show_page("home")
        self._set_status("Ready · local features available")
        self.root.deiconify()
        self.root.lift()
        self.root.after(250, self._auto_connect)

    def _clear_root(self):
        for child in self.root.winfo_children():
            child.destroy()
        self.pages = {}
        self.current = ""

    def toggle_theme(self):
        name = "dark" if self.preferences.get("theme", "light") == "light" else "light"
        self.apply_theme(name)

    def apply_theme(self, name: str):
        self.preferences["theme"] = name
        self.save_preferences()
        current = self.current
        self._clear_root()
        self.colors = apply_theme(self.root, name)
        if self.profile and self.store:
            self.open_workspace()
            if current and current in self.nav_buttons:
                self.show_page(current)
        else:
            self.show_unlock()

    def show_page(self, key: str):
        entry = next((item for item in NAVIGATION if item[0] == key), None)
        if entry is None:
            return
        _key, _label, factory, owner_only, subtitle = entry
        if owner_only and not self.is_owner():
            messagebox.showerror("Permission", "Administration requires the CORE owner account.")
            return
        if key not in self.pages:
            try:
                self.pages[key] = factory(self.content, self)
            except Exception as exc:
                _LOG.exception("Page creation failed: %s", key)
                self.show_error(exc)
                return
        for page_key, page in self.pages.items():
            if page_key == key:
                page.scroll.outer.pack(fill="both", expand=True)
            else:
                page.scroll.outer.pack_forget()
        self.current = key
        self.page_title.set(_label)
        self.page_subtitle.set(subtitle)
        for nav_key, widget in self.nav_buttons.items():
            widget.configure(
                background=self.colors["sidebar_active"] if nav_key == key else self.colors["sidebar"],
                foreground="#ffffff" if nav_key == key else self.colors["sidebar_text"],
            )
        try:
            self.pages[key].refresh()
        except Exception as exc:
            _LOG.exception("Page refresh failed: %s", key)
            self.show_error(exc)

    def refresh_current(self):
        page = self.pages.get(self.current)
        if page is not None:
            try:
                page.refresh()
            except Exception:
                _LOG.exception("Refresh failed")

    def _update_role_visibility(self):
        owner = self.is_owner()
        for key in self.nav_owner_only:
            widget = self.nav_buttons.get(key)
            if widget is None:
                continue
            if owner:
                widget.grid()
            else:
                widget.grid_remove()
        if not owner and self.current == "admin" and self.nav_buttons:
            self.show_page("home")

    def is_owner(self) -> bool:
        profile = self.connection.profile or {}
        if profile.get("role"):
            return profile["role"] == "owner"
        return self.binding_role() == "owner"

    def record_services(self) -> tuple:
        if self.is_owner():
            return ("workspace", "asis", "ascs", "tiviss", "rescs")
        return ("workspace", "asis")

    # ------------------------------------------------------------------ connection
    def _auto_connect(self):
        if self.closing or self.connection.state in (
            ConnectionState.ONLINE, ConnectionState.CONNECTING, ConnectionState.RECONNECTING
        ):
            return
        if os.environ.get("RISARMS_UI_NO_AUTO_CONNECT"):
            self._set_status("Offline · automatic connection disabled for this launch.")
            return
        binding = self.store.binding(self.profile)
        if not binding or not binding.get("server") or not binding.get("remote_id"):
            self._set_status("Offline · local features available. Sign in from Home or Connection.")
            return
        server, account = binding["server"], binding["remote_id"]
        credential = self.credentials.get(server, account)
        if not credential:
            self._set_status("Offline · sign in to CORE-HOST to renew this device.")
            return
        self._set_status("Connecting with remembered device credential…")

        def done(profile):
            if profile:
                self.on_connected(profile, None)
            else:
                self._set_status("Offline · device credential was not accepted.")

        self.run_async(lambda: self.connection.connect_device(server, account, credential), done,
                       lambda exc: self._set_status(f"Offline · {exc}"))

    def on_connected(self, profile: dict, password: str | None = None):
        profile = profile or {}
        if profile.get("role") == "pending":
            self._set_status("Pending owner approval")
            messagebox.showinfo("Access pending", "Your account request is waiting for the CORE owner to approve it.")
            self.refresh_current()
            return
        if not profile.get("id"):
            self.disconnect()
            self.show_error(APIError("The server did not return a usable account profile."))
            return
        server = self.connection.server
        try:
            self.store.bind(self.profile, server, str(profile["id"]), profile.get("role", "member"))
        except PermissionError as exc:
            self.disconnect()
            self.show_error(exc)
            return
        self.persist_server(server)
        self.account_label.set(f"{profile.get('name', profile.get('username', 'Account'))} · {profile.get('role', 'member')}")
        self._update_role_visibility()
        self._set_status(f"Online · {server}")
        if profile.get("password_change_required"):
            if password:
                PasswordChangeDialog.ask(self, profile.get("username", ""), password)
            else:
                messagebox.showwarning(
                    "Password change required",
                    "This account still uses a temporary password. Sign in with the password once to replace it.",
                )
                self.disconnect()
                return
        self.refresh_current()

    def _state_changed(self, state: ConnectionState):
        self.post_ui("state", state)

    def _event_received(self, event: dict):
        self.post_ui("event", event)

    def _handle_state(self, state: ConnectionState):
        kind = {
            ConnectionState.ONLINE: "good",
            ConnectionState.CONNECTING: "accent",
            ConnectionState.RECONNECTING: "warn",
            ConnectionState.PENDING_APPROVAL: "warn",
            ConnectionState.SYNC_PENDING: "warn",
            ConnectionState.AUTH_EXPIRED: "bad",
            ConnectionState.AUTH_REJECTED: "bad",
            ConnectionState.SERVER_UNAVAILABLE: "bad",
            ConnectionState.OFFLINE: "muted",
        }.get(state, "muted")
        try:
            self.pill.set(state.label, kind)
        except (AttributeError, tk.TclError):
            return
        self._set_status(state.label + (f" · {self.connection.server}" if self.connection.server else ""))
        profile = self.connection.profile
        if profile:
            self.account_label.set(f"{profile.get('name', profile.get('username', 'Account'))} · {profile.get('role', 'member')}")
            self.sidebar_account.set(f"{self.local_name}\n{profile.get('name', '')} ({profile.get('role', 'member')})")
        if state is ConnectionState.ONLINE:
            self._update_role_visibility()
        if state in (ConnectionState.AUTH_EXPIRED, ConnectionState.AUTH_REJECTED):
            self._set_status(f"{state.label} · sign in again from the Connection or Home page")
        self.refresh_current()

    def _handle_event(self, event: dict):
        kind = event.get("type", "")
        message = event.get("message") or event.get("text") or ""
        if kind == "session.revoked":
            self._set_status("Session revoked by the server. Sign in again.")
        elif kind == "connection.restored":
            # Resume eligible synchronization as soon as the channel is back.
            self._set_status(f"Reconnected · {self.connection.server}")
            self.root.after(250, self._sync_if_needed)
        elif kind == "session.refreshed":
            profile = event.get("profile") or {}
            if profile.get("role"):
                self._update_role_visibility()
        elif kind == "message.received":
            self.refresh_current()
        elif kind in ("notification", "announcement", "account.approved", "access.approved"):
            if message:
                self._set_status(message[:300])
            self.refresh_current()

    def disconnect(self):
        self.connection.disconnect(logout=True)
        self.account_label.set("No server account")
        self._set_status("Offline · local features remain available")
        self._update_role_visibility()
        self.refresh_current()

    def lock_profile(self):
        try:
            self.connection.close()
        except Exception:
            _LOG.exception("Connection close failed")
        try:
            self.store.close()
        except Exception:
            _LOG.exception("Store close failed")
        self.profile = ""
        self.local_name = ""
        self._clear_root()
        self.store = LocalStore(self.data_directory)
        self.show_unlock()

    # ------------------------------------------------------------------ async
    def post_ui(self, kind: str, payload):
        self.ui_queue.put((kind, payload))

    def run_async(self, work, done=None, error=None):
        def runner():
            try:
                value = work()
            except Exception as exc:
                self.post_ui("error", (error or self.show_error, exc))
                return
            self.post_ui("result", (done, value))

        threading.Thread(target=runner, daemon=True, name="risarms-worker").start()

    def _drain(self):
        if self.closing:
            return
        for _ in range(250):
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "result":
                    done, value = payload
                    if done is not None:
                        done(value)
                elif kind == "error":
                    error, exc = payload
                    (error or self.show_error)(exc)
                elif kind == "state":
                    self._handle_state(payload)
                elif kind == "event":
                    self._handle_event(payload)
                elif kind == "append":
                    page, text = payload
                    target = getattr(page, "append_output", None) or getattr(page, "_append_ui", None)
                    if target:
                        target(text)
                elif kind == "job_done":
                    page, result = payload
                    page.job_complete(result) if hasattr(page, "job_complete") else page.complete(result)
                elif kind == "job_error":
                    page, exc = payload
                    page.job_failed(exc) if hasattr(page, "job_failed") else page.fail(exc)
                elif kind == "call":
                    payload()
            except Exception:
                _LOG.exception("UI callback failed")
        self.root.after(60, self._drain)

    def _auto_sync(self):
        if self.closing:
            return
        try:
            self._sync_if_needed()
        finally:
            if not self.closing:
                self.root.after(60000, self._auto_sync)

    def pending_message_count(self) -> int:
        return self.store.pending_message_count(self.profile) if self.profile else 0

    def has_pending_work(self) -> bool:
        return bool(
            self.pending_record_count()
            or self.pending_file_count()
            or self.pending_message_count()
        )

    def _sync_if_needed(self):
        """Synchronize every durable queue, not just pending records."""
        if not self.profile:
            return
        if self.connection.state is not ConnectionState.ONLINE:
            return
        if not self.preferences.get("auto_sync", True):
            return
        if not self.has_pending_work():
            return
        # The timer and the reconnect event can fire together; a second worker
        # would re-upload the same pending files and messages.
        if self._sync_running:
            return
        self._sync_running = True
        self.run_async(self._sync_work, self._sync_finished, self._sync_failed)

    def _sync_work(self):
        remote = self.require_remote()
        result = {"saved": 0, "conflicts": [], "uploaded": 0, "failed_files": [], "messages_sent": 0}
        if self.pending_record_count():
            result.update(sync_records(self.store, self.profile, remote))
        if self.pending_file_count():
            result.update(sync_files(self.store, self.profile, remote))
        if self.pending_message_count():
            result["messages_sent"] = self.store.flush_messages(self.profile, remote)
        return result

    def _sync_failed(self, exc):
        self._sync_running = False
        _LOG.info("Automatic sync skipped: %s", exc)

    def _sync_finished(self, result):
        self._sync_running = False
        saved = result.get("saved", 0)
        conflicts = result.get("conflicts", [])
        uploaded = result.get("uploaded", 0)
        messages = result.get("messages_sent", 0)
        failed = result.get("failed_files", [])
        parts = []
        if saved:
            parts.append(f"{saved} record(s)")
        if uploaded:
            parts.append(f"{uploaded} file(s)")
        if messages:
            parts.append(f"{messages} message(s)")
        if parts:
            text = f"Synchronized {' · '.join(parts)}"
            if conflicts:
                text += f" · conflicts: {len(conflicts)}"
            if failed:
                text += f" · retrying {len(failed)} file(s)"
            self._set_status(text)
            self.refresh_current()

    def require_remote(self):
        if self.connection.state is not ConnectionState.ONLINE or self.connection.api is None:
            raise APIError("Connect to CORE-HOST for server features. Local data remains available.", status=0)
        return self.connection.api

    # ------------------------------------------------------------------ helpers
    def _set_status(self, text: str):
        try:
            self.status_var.set(text)
        except Exception:
            pass

    def show_error(self, exc):
        text = str(exc) or exc.__class__.__name__
        _LOG.warning("User-visible error: %s", text)
        try:
            self._set_status(text[:400])
            messagebox.showerror("RISARMS", text)
        except tk.TclError:
            pass

    def notify(self, title: str, message: str):
        self._set_status(message[:300])
        messagebox.showinfo(title, message)

    def _tk_error(self, exc_type, exc_value, exc_traceback):
        _LOG.error("Tk callback failed", exc_info=(exc_type, exc_value, exc_traceback))
        try:
            self._set_status(f"Interface error: {exc_value}")
        except Exception:
            pass

    def profile_name(self) -> str:
        return self.local_name or "—"

    def binding_role(self) -> str:
        binding = self.store.binding(self.profile) if self.profile else None
        return (binding or {}).get("remote_role") or "local"

    def pending_record_count(self) -> int:
        return len(self.store.records(self.profile, pending=True)) if self.profile else 0

    def pending_file_count(self) -> int:
        return sum(1 for row in self.store.files(self.profile) if row["pending"]) if self.profile else 0

    def local_history_count(self) -> int:
        return len(self.store.history(self.profile)) if self.profile else 0

    def hardware_report(self) -> dict:
        memory = 0
        try:
            if os.name == "posix":
                memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            elif os.name == "nt":
                import ctypes

                class MemoryStatus(ctypes.Structure):
                    _fields_ = [
                        ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                        ("total_page_file", ctypes.c_ulonglong), ("available_page_file", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                        ("available_extended_virtual", ctypes.c_ulonglong),
                    ]

                status = MemoryStatus()
                status.length = ctypes.sizeof(MemoryStatus)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
                memory = int(status.total)
        except (OSError, ValueError, AttributeError):
            memory = 0
        try:
            free = shutil.disk_usage(self.data_directory).free
        except OSError:
            free = 0
        return {
            "platform": f"{platform.system()} {platform.release()}",
            "machine": platform.machine(),
            "processor": platform.processor() or platform.machine(),
            "cpu_count": os.cpu_count() or 0,
            "memory_bytes": memory,
            "data_free_bytes": free,
            "python": platform.python_version(),
            "hostname": socket.gethostname(),
        }

    def close(self):
        if self.closing:
            return
        self.closing = True
        try:
            self.preferences["geometry"] = self.root.geometry()
            self.save_preferences()
        except Exception:
            pass
        try:
            self.connection.close()
        except Exception:
            _LOG.exception("Connection close failed")
        try:
            self.store.close()
        except Exception:
            _LOG.exception("Store close failed")
        try:
            self.root.destroy()
        except Exception:
            pass
        try:
            if self._lock is not None:
                self._lock.close()
        except Exception:
            pass
