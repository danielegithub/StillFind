# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Widget riutilizzabili."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from contextlib import closing
from datetime import date
from tkinter import ttk

import customtkinter as ctk

from stillfind.core import config
from stillfind.storage.protection import ArchiveProtection
from stillfind.ui.theme import PAL, action_icon


class PreviewAction(ctk.CTkButton):
    def __init__(self, master, action, label, command):
        primary = action == "open"
        self._tip_text = label
        self._tip = self._tip_job = None

        def activate():
            self._hide_tip()
            command()

        super().__init__(master, text="", image=action_icon(action), command=activate,
                         width=38, height=36, corner_radius=9, border_width=1, border_spacing=0,
                         fg_color=PAL["accent"] if primary else "transparent",
                         border_color=PAL["accent"] if primary else PAL["line"],
                         hover_color=PAL["accent_hover"] if primary else PAL["ghost_hover"])
        tk.Frame.configure(self, takefocus=True)
        self.bind("<Enter>", self._schedule_tip)
        self.bind("<Leave>", self._hide_tip)
        # CTkButton.bind indirizza gli eventi ai figli grafici. Il focus di Tab arriva alla cornice.
        tk.Misc.bind(self, "<FocusIn>", self._show_tip)
        for event in ("<FocusOut>", "<Escape>", "<Unmap>"):
            tk.Misc.bind(self, event, self._hide_tip)
        for key in ("<Return>", "<space>"):
            tk.Misc.bind(self, key, self._press)

    def _press(self, event):
        self.invoke()
        return "break"

    def _schedule_tip(self, event):
        if self._tip is None and self._tip_job is None:
            self._tip_job = self.after(450, self._show_tip)

    def _show_tip(self, event=None):
        self._hide_tip()
        top = self.winfo_toplevel()
        self._tip = tk.Label(top, text=self._tip_text, bg=col("text"), fg=col("card"),
                             padx=8, pady=4, takefocus=False)
        x = self.winfo_rootx() - top.winfo_rootx()
        y = self.winfo_rooty() - top.winfo_rooty()
        below = y + self.winfo_height() + 4
        h = self._tip.winfo_reqheight()
        self._tip.place(x=max(0, min(x, top.winfo_width() - self._tip.winfo_reqwidth())),
                        y=below if below + h <= top.winfo_height() else max(0, y - h - 4))
        self._tip.lift()

    def _hide_tip(self, event=None):
        if self._tip_job is not None:
            self.after_cancel(self._tip_job)
            self._tip_job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def destroy(self):
        self._hide_tip()
        super().destroy()


# --------------------------------------------------------------------------


def read_in_background(widget, work, display):
    # Tk rimane sul thread principale. Le finestre chiuse e le selezioni superate scartano la risposta.
    ticket = object()
    widget._request = ticket
    reply = queue.SimpleQueue()

    def worker():
        try:
            with closing(ArchiveProtection.connection(config.DB_PATH)) as con:
                con.execute("BEGIN")
                reply.put((work(con), None))
        except Exception as exc:
            reply.put((None, str(exc)))

    def poll():
        if widget._request is not ticket or not widget.winfo_exists():
            return
        try:
            data, error = reply.get_nowait()
        except queue.Empty:
            widget.after(50, poll)
        else:
            display(data, error)

    threading.Thread(target=worker, daemon=True).start()
    widget.after(50, poll)


def make_tree(parent, columns, branch_title=None):
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    frame.pack(fill="both", expand=True, padx=6, pady=4)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="tree headings" if branch_title else "headings", selectmode="browse",
                        style="Cerca.Treeview")
    if branch_title:
        tree.heading("#0", text=branch_title)
        tree.column("#0", width=205, minwidth=100)
    for key, title, width in columns:
        tree.heading(key, text=title)
        tree.column(key, width=width, minwidth=40, stretch=True)
    scroll = ttk.Scrollbar(frame, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    tree.pack(fill="both", expand=True)
    return tree


def short_date(iso):
    return date.fromisoformat(iso).strftime("%d/%m/%Y") if iso else "Data sconosciuta"
