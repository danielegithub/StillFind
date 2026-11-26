# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Criteri di ricerca della finestra."""
from __future__ import annotations

from stillfind.core import labels
from stillfind.core.i18n import mark, tr
from stillfind.tools.library import SavedSearches


class SearchCriteria:
    """I criteri di ricerca mostrati nella finestra: lettura, registrazione nella cronologia e ripristino.

    Si salvano identificatori interni («pdf», «week», «rank»…), non i testi mostrati: una ricerca salvata funziona in
    qualunque lingua dell'interfaccia. Le ricerche salvate in italiano dalle versioni precedenti si riconoscono ancora."""

    @staticmethod
    def of(app):
        return dict(query=app.qvar.get().strip(), category=app.type_choices.id(app.type_var.get(), "all"),
                    where=labels.WHERE_CHOICES.id(app.where_var.get(), "all"), order=app._order_id(),
                    date=app._date_id(), custom=app._date_custom, folder=app.folder_filter)

    @staticmethod
    def record(app):
        c = SearchCriteria.of(app)
        if c["query"] or c["folder"] or c["category"] != "all" or c["date"] != "any":
            if app.cfg.get("search_history", True):
                SavedSearches(app.con).save(c)

    @staticmethod
    def _identifier(value, choices: labels.Choices, default: str) -> str:
        """Identificatore da un valore salvato: già identificatore, oppure testo italiano di una versione precedente."""
        if value in choices.ids:
            return value
        return next((key for key, text in choices.items if text == value), default)

    @staticmethod
    def restore(app, c):
        categories = labels.Choices([("all", mark("Tutti i tipi"))] + list(labels.CATEGORY_NAMES.items))
        category = SearchCriteria._identifier(c["category"], categories, "all")
        if category not in app.type_choices.ids:  # tipo di documento non più presente nell'archivio
            category = "all"
        app.type_var.set(app.type_choices.label(category))
        app.where_var.set(labels.WHERE_CHOICES.label(SearchCriteria._identifier(c["where"], labels.WHERE_CHOICES, "all")))
        app.order_var.set(labels.ORDER_CHOICES.label(SearchCriteria._identifier(c["order"], labels.ORDER_CHOICES, "rank")))
        app._date_custom = c["custom"]
        custom = app._date_custom
        if c["date"] == "range" or (custom and c["date"] == custom[2]):
            app.date_var.set(custom[2])
        else:
            app.date_var.set(labels.DATE_CHOICES.label(SearchCriteria._identifier(c["date"], labels.DATE_CHOICES, "any")))
        app.folder_filter = c["folder"]
        label = next((k for k, v in app._folder_map.items() if v == c["folder"]), c["folder"] or tr("Tutte le cartelle"))
        app.folder_var.set(label)
        app.qvar.set(c["query"])
        app.do_search(force=True)
