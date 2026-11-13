from __future__ import annotations

import base64
import hashlib
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..remote import sync_files
from ..widgets import button
from ..widgets import row as action_row
from .base import Page

MAX_FILE = 8 * 1024 * 1024
ALL_FILES = "All files"


class FilesPage(Page):
    title = "Cloud Files"

    def build(self):
        ttk.Label(self.body, text="Cloud Files", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="Up to 8 MiB per file. Uploads and downloads are SHA-256 verified.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 12))

        folder_row = action_row(self.body)
        ttk.Label(folder_row, text="Folder").pack(side="left", padx=(0, 6))
        self.folder_choice = ttk.Combobox(folder_row, state="readonly", width=28, values=[ALL_FILES])
        self.folder_choice.set(ALL_FILES)
        self.folder_choice.pack(side="left", padx=(0, 8))
        self.folder_choice.bind("<<ComboboxSelected>>", lambda event: self.refresh_server())
        button(folder_row, "New folder", self.create_folder)

        ttk.Label(self.body, text="Local files and pending uploads", style="Subheading.TLabel").pack(anchor="w")
        self.local_list = tk.Listbox(self.body, width=1, height=5)
        self.local_list.pack(fill="x", pady=5)
        local_actions = action_row(self.body)
        button(local_actions, "Add local file", self.add_file, accent=True)
        button(local_actions, "Export cached file", self.export_file)
        button(local_actions, "Upload pending now", self.sync_files)

        ttk.Label(self.body, text="My server files", style="Subheading.TLabel").pack(anchor="w", pady=(18, 4))
        self.cloud_list = tk.Listbox(self.body, width=1, height=8)
        self.cloud_list.pack(fill="both", expand=True)
        cloud_actions = action_row(self.body)
        button(cloud_actions, "Refresh", self.refresh_server)
        button(cloud_actions, "Details", self.show_details)
        button(cloud_actions, "Cache selected", self.cache_file)
        button(cloud_actions, "Move here", self.move_here)
        button(cloud_actions, "Delete selected", self.delete_file)

        self.progress = ttk.Progressbar(self.body, mode="indeterminate")
        self.progress.pack(fill="x", pady=(10, 0))
        self.note = tk.StringVar(value="")
        ttk.Label(self.body, textvariable=self.note, style="Muted.TLabel", wraplength=820).pack(anchor="w", pady=8)
        self.list_local()

    # ------------------------------------------------------------------ local
    def list_local(self):
        self.local_rows = self.app.store.files(self.app.profile)
        self.local_list.delete(0, "end")
        used = 0
        for row in self.local_rows:
            path = Path(row["path"])
            if path.is_file():
                used += path.stat().st_size
            self.local_list.insert("end", row["name"] + (" [pending]" if row["pending"] else " [cached]"))
        pending = sum(1 for row in self.local_rows if row["pending"])
        self.note.set(f"Local cache: {used // 1024} KiB of 250 MiB" + (f" · {pending} pending upload(s)" if pending else ""))

    def add_file(self):
        path = filedialog.askopenfilename()
        if not path:
            return
        try:
            data = Path(path).read_bytes()
            if len(data) > MAX_FILE:
                raise ValueError("Files must be at most 8 MiB.")
            self.app.store.add_file(self.app.profile, Path(path).name, data)
            self.list_local()
        except Exception as exc:
            messagebox.showerror("File", str(exc))

    def export_file(self):
        selected = self.local_list.curselection()
        if not selected:
            return
        row = self.local_rows[selected[0]]
        target = filedialog.asksaveasfilename(initialfile=row["name"])
        if target:
            shutil.copyfile(row["path"], target)

    def sync_files(self):
        if not self.app.connection.state.connected:
            self.note.set("Offline · uploads stay queued and are retried automatically after reconnect.")
            return
        self.progress.start(12)

        def work():
            return sync_files(self.app.store, self.app.profile, self.app.require_remote())

        def done(result):
            self.progress.stop()
            self.list_local()
            failed = result.get("failed_files", [])
            self.note.set(
                f"Uploaded {result.get('uploaded', 0)} file(s)"
                + (f" · {len(failed)} will retry: {', '.join(failed[:3])}" if failed else "")
            )
            self.refresh_server()

        def error(exc):
            self.progress.stop()
            self.app.show_error(exc)

        self.app.run_async(work, done, error)

    # ----------------------------------------------------------------- server
    def selected_folder(self):
        name = self.folder_choice.get()
        return (getattr(self, "folder_map", {}) or {}).get(name)

    def refresh_server(self):
        if not self.app.connection.state.connected:
            self.note.set("Offline · connect to CORE-HOST to list cloud files.")
            return
        self.progress.start(12)

        def work():
            remote = self.app.require_remote()
            folders = remote.request("GET", "/api/folders")
            folder = self.selected_folder()
            rows, offset, quota = [], 0, 0
            while offset < 10000:
                suffix = f"&folder={folder}" if folder else ""
                page = remote.request("GET", f"/api/files?offset={offset}{suffix}")
                rows.extend(page["items"])
                quota = page.get("quota_bytes", quota)
                offset += len(page["items"])
                if offset >= page.get("total", offset):
                    return rows, folders, quota
                if not page["items"]:
                    raise ValueError("Incomplete cloud file page.")
            raise ValueError("Cloud file scan limit reached.")

        def done(result):
            self.progress.stop()
            rows, folders, quota = result
            self.cloud_rows = rows
            self.apply_folders(folders)
            self.cloud_list.delete(0, "end")
            for row in rows:
                self.cloud_list.insert("end", f"{row['filename']} · {row['size']} bytes")
            used = sum(int(row.get("size", 0)) for row in rows)
            self.note.set(f"{len(rows)} file(s) · {used // 1024} KiB of {quota // 1024} KiB quota used")

        def error(exc):
            self.progress.stop()
            self.app.show_error(exc)

        self.app.run_async(work, done, error)

    def apply_folders(self, payload):
        folders = list(payload.get("folders", [])) + list(payload.get("shared_folders", []))
        self.folder_map = {ALL_FILES: None}
        for folder in folders:
            self.folder_map[folder["name"]] = folder["id"]
        current = self.folder_choice.get()
        self.folder_choice.configure(values=list(self.folder_map))
        if current in self.folder_map:
            self.folder_choice.set(current)
        else:
            self.folder_choice.set(ALL_FILES)

    def create_folder(self):
        name = simpledialog.askstring("New folder", "Folder name", parent=self.app.root)
        if not name:
            return
        self.app.run_async(
            lambda: self.app.require_remote().request("POST", "/api/folders", {"name": name}),
            lambda _: self.refresh_server(),
            self.app.show_error,
        )

    def show_details(self):
        selected = self.cloud_list.curselection()
        if not selected or not getattr(self, "cloud_rows", None):
            return
        row = self.cloud_rows[selected[0]]
        details = [
            f"Name: {row.get('filename', row.get('name', ''))}",
            f"Size: {row.get('size', 0)} bytes",
            f"SHA-256: {row.get('sha256', 'unavailable')}",
            f"Folder: {self.folder_choice.get()}",
        ]
        messagebox.showinfo("File details", "\n".join(details))

    def move_here(self):
        selected = self.cloud_list.curselection()
        folder = self.selected_folder()
        if not selected or not folder:
            self.note.set("Choose a folder other than 'All files' to move a file into it.")
            return
        row = self.cloud_rows[selected[0]]
        self.app.run_async(
            lambda: self.app.require_remote().request(
                "POST", f"/api/files/{row['id']}/move", {"folder": folder}
            ),
            lambda _: self.refresh_server(),
            self.app.show_error,
        )

    def cache_file(self):
        selected = self.cloud_list.curselection()
        if not selected:
            return
        row = self.cloud_rows[selected[0]]
        self.progress.start(12)

        def work():
            result = self.app.require_remote().request("GET", "/api/files/" + row["id"])
            data = base64.b64decode(result["base64"], validate=True)
            if hashlib.sha256(data).hexdigest() != result["metadata"]["sha256"]:
                raise ValueError("File integrity check failed.")
            self.app.store.add_file(self.app.profile, row["filename"], data, row["id"])
            return True

        def done(_result):
            self.progress.stop()
            self.list_local()

        def error(exc):
            self.progress.stop()
            self.app.show_error(exc)

        self.app.run_async(work, done, error)

    def delete_file(self):
        selected = self.cloud_list.curselection()
        if not selected:
            return
        row = self.cloud_rows[selected[0]]
        if not messagebox.askyesno("Delete cloud file", f"Delete {row['filename']} from cloud storage? Local copies remain."):
            return
        self.app.run_async(
            lambda: self.app.require_remote().request("POST", f"/api/files/{row['id']}/delete"),
            lambda _: self.refresh_server(), self.app.show_error,
        )

    def refresh(self):
        self.list_local()
