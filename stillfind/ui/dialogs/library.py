# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Cronologia e ricerche salvate."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
from stillfind.tools.library import SavedSearches
from stillfind.ui.search_criteria import SearchCriteria


class LibraryDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Cronologia e ricerche salvate")
        self.geometry("660x420")
        self.transient(app)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=8)
        self.lists, self.values = [], {}
        for title in ("Cronologia", "Ricerche salvate"):
            tree = ttk.Treeview(self.tabs, show="headings", columns=("label","query"))
            tree.heading("label", text="Ricerca")
            tree.heading("query", text="Testo")
            self.tabs.add(tree, text=title)
            self.lists.append(tree)
            tree.bind("<Double-1>", lambda _: self.open())
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=5)
        for text, cmd in (("Apri",self.open),("Salva ricerca corrente…",self.save),
                          ("Elimina",self.remove),("Svuota cronologia",self.clear)):
            ttk.Button(bar,text=text,command=cmd).pack(side="left")
        self.enabled = tk.BooleanVar(value=app.cfg.get("search_history",True))
        ttk.Checkbutton(self,text="Ricorda le mie ricerche su questo computer",variable=self.enabled,
                        command=self.toggle).pack(anchor="w",padx=12,pady=6)
        self.refresh()

    def refresh(self):
        self.values.clear()
        for i, tree in enumerate(self.lists):
            tree.delete(*tree.get_children())
            for ident, name, c in SavedSearches(self.app.con).listing(bool(i)):
                tree.insert("", "end", iid=str(ident), values=(name or c["folder"] or c["category"], c["query"]))
                self.values[str(ident)] = c

    def selection(self):
        return self.lists[self.tabs.index("current")].selection()

    def open(self):
        sel = self.selection()
        if sel:
            c = self.values[sel[0]]
            self.destroy()
            SearchCriteria.restore(self.app,c)

    def save(self):
        name = simpledialog.askstring("Salva ricerca","Nome della ricerca",parent=self)
        if name and name.strip():
            SavedSearches(self.app.con).save(SearchCriteria.of(self.app), name)
            self.refresh()
            self.tabs.select(1)

    def remove(self):
        sel = self.selection()
        if sel:
            with self.app.con:
                self.app.con.execute("DELETE FROM ricerche WHERE id=?", (sel[0],))
            self.refresh()

    def clear(self):
        if messagebox.askyesno("Cronologia","Cancellare le ricerche recenti?",parent=self):
            self.app._history_signature = SearchCriteria.of(self.app)
            with self.app.con:
                self.app.con.execute("DELETE FROM ricerche WHERE nome IS NULL")
            self.refresh()

    def toggle(self):
        self.app.cfg["search_history"] = self.enabled.get()
        self.app.cfg.save()
