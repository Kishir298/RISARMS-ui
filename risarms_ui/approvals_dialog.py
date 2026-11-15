"""Modal approval prompt shown for local agent tool calls."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk


def ask(parent, payload):
    dialog = tk.Toplevel(parent)
    dialog.title("Approve local action")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)
    decision = {"value": "deny"}

    frame = tk.Frame(dialog, padx=18, pady=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="A local service wants to perform an action", style="Heading.TLabel").pack(anchor="w")
    ttk.Label(
        frame,
        text="Review the request. Closing this window, pressing Escape, or timing out denies it.",
        style="Muted.TLabel",
        wraplength=560,
    ).pack(anchor="w", pady=(4, 12))

    rows = [
        ("Service", payload.get("service", "local service")),
        ("Action", payload.get("action", "unknown")),
        ("Workspace", payload.get("workspace", "not reported")),
        ("Command", payload.get("command", "")),
        ("Details", json.dumps(payload.get("details", {}), indent=2, sort_keys=True)[:4000]),
    ]
    for label, value in rows:
        if not value:
            continue
        ttk.Label(frame, text=label, style="Surface.TLabel").pack(anchor="w", pady=(6, 2))
        widget = tk.Text(frame, width=72, height=1 if label != "Details" else 8, wrap="word")
        widget.insert("1.0", str(value))
        widget.configure(state="disabled")
        widget.pack(fill="x")

    actions = tk.Frame(frame)
    actions.pack(fill="x", pady=(14, 0))

    def choose(value):
        decision["value"] = value
        dialog.destroy()

    ttk.Button(actions, text="Allow once", style="Accent.TButton", command=lambda: choose("allow_once")).pack(side="left", padx=(0, 8))
    ttk.Button(actions, text="Allow for this session", command=lambda: choose("allow_session")).pack(side="left", padx=(0, 8))
    ttk.Button(actions, text="Deny", command=lambda: choose("deny")).pack(side="left")
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose("deny"))
    dialog.bind("<Escape>", lambda _event: choose("deny"))
    dialog.update_idletasks()
    dialog.geometry(f"+{parent.winfo_rootx() + 80}+{parent.winfo_rooty() + 80}")
    dialog.wait_window()
    return decision["value"]
