# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Elenco di documenti."""
from __future__ import annotations

from datetime import date, timedelta
import customtkinter as ctk
from stillfind.indexing.relations import RelationGraph
from stillfind.ui.widgets import read_in_background, make_tree, short_date


class DocumentList(ctk.CTkToplevel):
    def __init__(self, app, entity=None):
        super().__init__(app)
        self.app, self.entity = app, entity
        self.title("Scheda dell’entità" if entity else "Scadenze")
        self.geometry("850x470")
        self.transient(app)
        self.items = {}
        self.info = ctk.CTkLabel(self, text=entity[1] if entity else "Scadenze dei documenti", anchor="w")
        self.info.pack(fill="x", padx=12, pady=8)
        if not entity:
            self.period = ctk.CTkSegmentedButton(self, values=["Prossimi 30 giorni", "Future", "Tutte"],
                                                 command=lambda _: self.refresh())
            self.period.set("Prossimi 30 giorni")
            self.period.pack(anchor="w", padx=12)
        columns = [("data", "Data del documento" if entity else "Scadenza", 105), ("nome", "Documento", 260),
                   ("fonte", "Origine della data" if entity else "Passaggio", 305)]
        self.tree = make_tree(self, columns)
        self.tree.bind("<Double-1>", lambda _: self.open())
        self.tree.bind("<Return>", lambda _: self.open())
        self.btn = ctk.CTkButton(self, text="Apri il passaggio", command=self.open)
        self.btn.pack(anchor="e", padx=12, pady=8)
        self.refresh()

    def refresh(self):
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        self.btn.configure(state="disabled")
        entity = self.entity
        period = None if entity else self.period.get()
        today = date.today()
        def work(con):
            if entity:
                return RelationGraph(con).entity_documents(entity[0])
            return RelationGraph(con).deadlines(today.isoformat() if period != "Tutte" else None,
                                       (today + timedelta(days=30)).isoformat() if period == "Prossimi 30 giorni" else None)
        read_in_background(self, work, self._display)

    def _display(self, data, error):
        if error:
            self.info.configure(text=error)
            return
        for row in data:
            if self.entity:
                doc, name, stamp, why, a, b = row
            else:
                stamp, doc, name, why, a, b = row
            key = self.tree.insert("", "end", values=(short_date(stamp), name, why))
            self.items[key] = doc, a, b
        self.info.configure(text=(self.entity[1] + " · Cronologia della pratica" if self.entity else
                                  ("Scadenze dei documenti" if data else "Nessuna scadenza nel periodo scelto.")))
        if self.items:
            self.tree.selection_set(next(iter(self.items)))
            self.btn.configure(state="normal")

    def open(self):
        item = self.items.get(next(iter(self.tree.selection()), ""))
        if item:
            self.app.open_indexed(*item)
            self.destroy()
