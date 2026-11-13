from __future__ import annotations

import base64
import hashlib
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..widgets import button
from ..widgets import row as action_row
from .base import Page

MAX_FILE = 8 * 1024 * 1024


class FilesPage(Page):
    title = "Cloud Files"

    def build(self):
        ttk.Label(self.body, text="Cloud Files", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="Up to 8 MiB per file. Uploads and downloads are SHA-256 verified.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 12))
        ttk.Label(self.body, text="Local files and pending uploads", style="Subheading.TLabel").pack(anchor="w")
        self.local_list = tk.Listbox(self.body, width=1, height=5)
        self.local_list.pack(fill="x", pady=5)
        local_actions = action_row(self.body)
        button(local_actions, "Add local file", self.add_file, accent=True)
        button(local_actions, "Export cached file", self.export_file)
        button(local_actions, "Sync pending uploads", self.sync_files)

        ttk.Label(self.body, text="My server files", style="Subheading.TLabel").pack(anchor="w", pady=(18, 4))
        self.cloud_list = tk.Listbox(self.body, width=1, height=8)
        self.cloud_list.pack(fill="both", expand=True)
        cloud_actions = action_row(self.body)
        button(cloud_actions, "Refresh", self.refresh_server)
        button(cloud_actions, "Cache selected", self.cache_file)
        button(cloud_actions, "Delete selected", self.delete_file)
        self.note = tk.StringVar(value="")
        ttk.Label(self.body, textvariable=self.note, style="Muted.TLabel").pack(anchor="w", pady=8)
        self.list_local()

    def list_local(self):
        self.local_rows = self.app.store.files(self.app.profile)
        self.local_list.delete(0, "end")
        used = 0
        for row in self.local_rows:
            path = Path(row["path"])
            if path.is_file():
                used += path.stat().st_size
            self.local_list.insert("end", row["name"] + (" [pending]" if row["pending"] else " [cached]"))
        self.note.set(f"Local cache: {used // 1024} KiB of 250 MiB")

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
        def work():
            remote = self.app.require_remote()
            count = 0
            for row in self.app.store.files(self.app.profile):
                if row["pending"]:
                    data = Path(row["path"]).read_bytes()
                    meta = remote.request("POST", "/api/files", {
                        "name": row["name"], "upload_id": row["id"],
                        "base64": base64.b64encode(data).decode(),
                    })
                    self.app.store.file_ack(self.app.profile, row["id"], meta["id"])
                    count += 1
            return count

        self.app.run_async(work, lambda count: (self.list_local(), messagebox.showinfo("Files synced", f"{count} file(s) uploaded")),
                           self.app.show_error)

    def refresh_server(self):
        def work():
            rows, offset, remote = [], 0, self.app.require_remote()
            while offset < 10000:
                page = remote.request("GET", f"/api/files?offset={offset}")
                rows.extend(page["items"])
                offset += len(page["items"])
                if offset >= page.get("total", offset):
                    return rows
                if not page["items"]:
                    raise ValueError("Incomplete cloud file page.")
            raise ValueError("Cloud file scan limit reached.")

        def done(rows):
            self.cloud_rows = rows
            self.cloud_list.delete(0, "end")
            for row in rows:
                self.cloud_list.insert("end", f"{row['filename']} · {row['size']} bytes")

        self.app.run_async(work, done, self.app.show_error)

    def cache_file(self):
        selected = self.cloud_list.curselection()
        if not selected:
            return
        row = self.cloud_rows[selected[0]]

        def work():
            result = self.app.require_remote().request("GET", "/api/files/" + row["id"])
            data = base64.b64decode(result["base64"], validate=True)
            if hashlib.sha256(data).hexdigest() != result["metadata"]["sha256"]:
                raise ValueError("File integrity check failed.")
            self.app.store.add_file(self.app.profile, row["filename"], data, row["id"])
            return True

        self.app.run_async(work, lambda _: self.list_local(), self.app.show_error)

    def delete_file(self):
        selected = self.cloud_list.curselection()
        if not selected:
            return
        row = self.cloud_rows[selected[0]]
        if not messagebox.askyesno("Delete cloud file", f"Delete {row['filename']} from cloud storage? Local copies remain."):
            return
        self.app.run_async(
            lambda: self.app.require_remote().request("POST", f"/api/files/{row['id']}/delete"),
            lambda _: self.cloud_list.delete(selected[0]), self.app.show_error,
        )
