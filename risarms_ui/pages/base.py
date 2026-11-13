from __future__ import annotations

from ..widgets import ScrollableFrame


class Page:
    title = "Page"
    owner_only = False

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.colors = app.colors
        self.scroll = ScrollableFrame(parent, self.colors["bg"])
        self.scroll.pack(fill="both", expand=True)
        self.body = self.scroll.content
        self.build()

    def build(self):
        pass

    def refresh(self):
        pass

    def set_text(self, widget, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def append(self, widget, text: str):
        widget.configure(state="normal")
        widget.insert("end", text)
        widget.see("end")
        widget.configure(state="disabled")
