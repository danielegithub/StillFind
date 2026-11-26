# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Cronologia e ricerche salvate."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from stillfind.core import labels
from stillfind.core.i18n import mark, tr
from stillfind.tools.library import SavedSearches
from stillfind.ui.search_criteria import SearchCriteria
from stillfind.ui.theme import font, ghost_button, primary_button
from stillfind.ui.widgets import IdSelector, SquareDialog, SquareToggle, TreeFrame, ask_text


class LibraryDialog(SquareDialog):
    TABS = ("history", "saved")  # identificatori; i testi mostrati sono tradotti
    TAB_NAMES = labels.Choices((("history", mark("Cronologia")), ("saved", mark("Ricerche salvate"))))

    def __init__(self, app):
        super().__init__(app, tr("Cronologia e ricerche salvate"), "700x470", minsize=(600, 380))
        self.app = app
        self.enabled = tk.BooleanVar(value=app.cfg.get("search_history", True))
        SquareToggle(self, text=tr("Ricorda le mie ricerche su questo computer"), variable=self.enabled,
                     command=self.toggle, font=font(13)).pack(side="bottom", anchor="w", padx=self.MARGIN,
                                                              pady=(4, self.MARGIN))
        bar = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        bar.pack(side="bottom", fill="x", padx=self.MARGIN, pady=(10, 8))
        primary_button(bar, tr("Apri"), self.open).pack(side="left")
        for text, cmd in ((tr("Salva ricerca corrente…"), self.save), (tr("Elimina"), self.remove),
                          (tr("Svuota cronologia"), self.clear)):
            ghost_button(bar, text, cmd).pack(side="left", padx=(8, 0))
        self.tabs = IdSelector(self, list(zip(self.TABS, self.TAB_NAMES.labels())), command=self._show_tab,
                               font=font(12), height=36)
        self.tabs.pack(fill="x", padx=self.MARGIN, pady=(self.MARGIN, 8))
        self.holder = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        self.holder.pack(fill="both", expand=True, padx=self.MARGIN)
        self.holder.grid_columnconfigure(0, weight=1)
        self.holder.grid_rowconfigure(0, weight=1)
        self.tables, self.lists, self.values = [], [], {}
        for _ in self.TABS:
            table = TreeFrame(self.holder, [("label", tr("Ricerca"), 240), ("query", tr("Testo"), 380)])
            table.grid(row=0, column=0, sticky="nsew")
            table.tree.bind("<Double-1>", lambda _: self.open())
            table.tree.bind("<Return>", lambda _: self.open())
            self.tables.append(table)
            self.lists.append(table.tree)
        self._show_tab(self.TABS[0])
        self.refresh()

    def _show_tab(self, name):
        self.tabs.set(name)
        for i, table in enumerate(self.tables):
            if self.TABS[i] == name:
                table.grid()
            else:
                table.grid_remove()

    def _current_tab(self):
        return self.TABS.index(self.tabs.get())

    def refresh(self):
        self.values.clear()
        for i, tree in enumerate(self.lists):
            tree.delete(*tree.get_children())
            for ident, name, c in SavedSearches(self.app.con).listing(bool(i)):
                tree.insert("", "end", iid=str(ident), values=(name or c["folder"] or self._category_name(c["category"]), c["query"]))
                self.values[str(ident)] = c

    @staticmethod
    def _category_name(value):
        """Nome della categoria di una ricerca salvata (identificatore, o testo italiano di una versione precedente)."""
        choices = labels.Choices([("all", mark("Tutti i tipi"))] + list(labels.CATEGORY_NAMES.items))
        return choices.label(value) if value in choices.ids else tr(value)

    def selection(self):
        return self.lists[self._current_tab()].selection()

    def open(self):
        sel = self.selection()
        if sel:
            c = self.values[sel[0]]
            self.destroy()
            SearchCriteria.restore(self.app, c)

    def save(self):
        name = ask_text(self, tr("Salva ricerca"), tr("Nome della ricerca"), ok=tr("Salva"))
        if name and name.strip():
            SavedSearches(self.app.con).save(SearchCriteria.of(self.app), name)
            self.refresh()
            self._show_tab(self.TABS[1])

    def remove(self):
        sel = self.selection()
        if sel:
            with self.app.con:
                self.app.con.execute("DELETE FROM ricerche WHERE id=?", (sel[0],))
            self.refresh()

    def clear(self):
        if messagebox.askyesno(tr("Cronologia"), tr("Cancellare le ricerche recenti?"), parent=self):
            self.app._history_signature = SearchCriteria.of(self.app)
            with self.app.con:
                self.app.con.execute("DELETE FROM ricerche WHERE nome IS NULL")
            self.refresh()

    def toggle(self):
        self.app.cfg["search_history"] = self.enabled.get()
        self.app.cfg.save()
