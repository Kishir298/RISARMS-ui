"""Reusable Tk widgets and responsive layout helpers."""
from __future__ import annotations


class ScrollableFrame:
    """A vertically scrollable frame that expands with the window."""

    def __init__(self, parent, background: str):
        import tkinter as tk
        from tkinter import ttk

        self.outer = ttk.Frame(parent)
        self.canvas = tk.Canvas(self.outer, highlightthickness=0, background=background)
        self.scrollbar = ttk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview)
        self.content = tk.Frame(self.canvas, background=background)
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content.bind("<Configure>", self._on_content)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_content(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def pack(self, **kwargs):
        self.outer.pack(**kwargs)
        return self

    def bind_mousewheel(self):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", self._wheel)
        self.canvas.bind_all("<Button-5>", self._wheel)

    def _wheel(self, event):
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")


class Card:
    def __init__(self, parent, title: str, colors: dict, subtitle: str = ""):
        import tkinter as tk
        from tkinter import ttk

        self.frame = tk.Frame(parent, background=colors["surface"], highlightthickness=1,
                              highlightbackground=colors["border"], bd=0)
        self.frame.pack(fill="x", pady=(0, 10))
        self.inner = tk.Frame(self.frame, background=colors["surface"])
        self.inner.pack(fill="both", expand=True, padx=14, pady=12)
        ttk.Label(self.inner, text=title, style="Surface.TLabel", font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        if subtitle:
            ttk.Label(self.inner, text=subtitle, style="SurfaceMuted.TLabel", wraplength=820).pack(anchor="w", pady=(2, 8))
        self.body = tk.Frame(self.inner, background=colors["surface"])
        self.body.pack(fill="both", expand=True)


class StatusPill:
    def __init__(self, parent, colors: dict):
        import tkinter as tk

        self.label = tk.Label(parent, text="Offline", padx=10, pady=3, bd=0,
                              background=colors["surface_alt"], foreground=colors["text"],
                              font=("TkDefaultFont", 10, "bold"))
        self._colors = colors

    def set(self, text: str, kind: str = "muted"):
        color = {
            "good": self._colors["good"],
            "warn": self._colors["warn"],
            "bad": self._colors["bad"],
            "accent": self._colors["accent"],
            "muted": self._colors["muted"],
        }.get(kind, self._colors["muted"])
        self.label.configure(text=text, foreground="#ffffff", background=color)


def field(parent, label: str, value: str = "", secret: bool = False, colors=None):
    from tkinter import ttk

    ttk.Label(parent, text=label, style="Surface.TLabel").pack(anchor="w", pady=(8, 3))
    entry = ttk.Entry(parent, width=1, show="*" if secret else "")
    entry.insert(0, value)
    entry.pack(fill="x")
    return entry


def row(parent):
    import tkinter as tk
    from tkinter import ttk

    try:
        background = parent.cget("background")
    except Exception:
        background = ttk.Style().lookup("TFrame", "background") or "#f4f6f8"
    frame = tk.Frame(parent, background=background)
    frame.pack(fill="x", pady=(4, 0))
    return frame


def button(parent, text: str, command, accent: bool = False):
    from tkinter import ttk

    widget = ttk.Button(parent, text=text, command=command, style="Accent.TButton" if accent else "TButton")
    widget.pack(side="left", padx=(0, 7))
    return widget
