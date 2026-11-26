# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Elenco di documenti."""
from __future__ import annotations

from datetime import date, timedelta

import customtkinter as ctk

from stillfind.core import labels
from stillfind.core.i18n import mark, tr
from stillfind.indexing.relations import RelationGraph
from stillfind.ui.theme import PAL, font, primary_button
from stillfind.ui.widgets import IdSelector, SquareDialog, make_tree, read_in_background, short_date


class DocumentList(SquareDialog):
    def __init__(self, app, entity=None):
        super().__init__(app, tr("Scheda dell’entità") if entity else tr("Scadenze"), "880x500", minsize=(680, 360))
        self.app, self.entity = app, entity
        self.items = {}
        self.btn = primary_button(self, tr("Apri il passaggio"), self.open)
        self.button_bar(self.btn)
        self.info = ctk.CTkLabel(self, text=entity[1] if entity else tr("Scadenze dei documenti"), anchor="w",
                                 font=font(15, True), text_color=PAL["text"])
        self.info.pack(fill="x", padx=self.MARGIN, pady=(self.MARGIN, 8))
        if not entity:
            periods = labels.Choices((("next30", mark("Prossimi 30 giorni")), ("future", mark("Future")),
                                      ("all", mark("Tutte"))))
            self.period = IdSelector(self, list(zip(periods.ids, periods.labels())), font=font(12),
                                     height=34, command=lambda _: self.refresh())
            self.period.set("next30")
            self.period.pack(anchor="w", padx=self.MARGIN, pady=(0, 8))
        columns = [("data", tr("Data del documento") if entity else tr("Scadenza"), 125), ("nome", tr("Documento"), 260),
                   ("fonte", tr("Origine della data") if entity else tr("Passaggio"), 305)]
        self.tree = make_tree(self, columns, framed=True)
        self.tree.bind("<Double-1>", lambda _: self.open())
        self.tree.bind("<Return>", lambda _: self.open())
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
            return RelationGraph(con).deadlines(today.isoformat() if period != "all" else None,
                                       (today + timedelta(days=30)).isoformat() if period == "next30" else None)
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
        self.info.configure(text=(self.entity[1] + " · " + tr("Cronologia della pratica") if self.entity else
                                  (tr("Scadenze dei documenti") if data else tr("Nessuna scadenza nel periodo scelto."))))
        if self.items:
            self.tree.selection_set(next(iter(self.items)))
            self.btn.configure(state="normal")

    def open(self):
        item = self.items.get(next(iter(self.tree.selection()), ""))
        if item:
            self.app.open_indexed(*item)
            self.destroy()
