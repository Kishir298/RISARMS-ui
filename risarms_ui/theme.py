"""Consistent light and dark theme for the desktop workspace."""
from __future__ import annotations

PALETTES = {
    "light": {
        "bg": "#f4f6f8",
        "surface": "#ffffff",
        "surface_alt": "#eef1f4",
        "text": "#17202a",
        "muted": "#5b6b7b",
        "border": "#d5dce3",
        "accent": "#1f6feb",
        "accent_text": "#ffffff",
        "good": "#1a7f37",
        "warn": "#9a6700",
        "bad": "#b42318",
        "sidebar": "#101826",
        "sidebar_text": "#d7dee8",
        "sidebar_active": "#1f6feb",
    },
    "dark": {
        "bg": "#0d1117",
        "surface": "#161b22",
        "surface_alt": "#1f2630",
        "text": "#e6edf3",
        "muted": "#9aa7b4",
        "border": "#30363d",
        "accent": "#388bfd",
        "accent_text": "#ffffff",
        "good": "#3fb950",
        "warn": "#d29922",
        "bad": "#f85149",
        "sidebar": "#080c12",
        "sidebar_text": "#c9d1d9",
        "sidebar_active": "#1f6feb",
    },
}


def palette(name: str) -> dict:
    return PALETTES.get(name, PALETTES["light"])


def apply_theme(root, name: str = "light") -> dict:
    from tkinter import ttk

    colors = palette(name)
    try:
        root.configure(bg=colors["bg"])
    except Exception:
        pass
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure(".", background=colors["bg"], foreground=colors["text"], fieldbackground=colors["surface"])
    style.configure("TFrame", background=colors["bg"])
    style.configure("Surface.TFrame", background=colors["surface"])
    style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
    style.configure("Surface.TLabel", background=colors["surface"], foreground=colors["text"])
    style.configure("Muted.TLabel", background=colors["bg"], foreground=colors["muted"])
    style.configure("SurfaceMuted.TLabel", background=colors["surface"], foreground=colors["muted"])
    style.configure("Title.TLabel", font=("TkDefaultFont", 22, "bold"))
    style.configure("Heading.TLabel", font=("TkDefaultFont", 15, "bold"))
    style.configure("Subheading.TLabel", font=("TkDefaultFont", 11, "bold"))
    style.configure("TButton", padding=7, background=colors["surface_alt"], foreground=colors["text"])
    style.map("TButton", background=[("active", colors["border"])])
    style.configure("Accent.TButton", padding=7, background=colors["accent"], foreground=colors["accent_text"])
    style.map("Accent.TButton", background=[("active", colors["accent"])])
    style.configure("TEntry", fieldbackground=colors["surface"], foreground=colors["text"])
    style.configure("TCombobox", fieldbackground=colors["surface"], foreground=colors["text"])
    style.configure("Treeview", background=colors["surface"], fieldbackground=colors["surface"], foreground=colors["text"])
    style.configure("TNotebook", background=colors["bg"])
    style.configure("TNotebook.Tab", padding=(12, 6))
    style.configure("TSeparator", background=colors["border"])
    return colors
