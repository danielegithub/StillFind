# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Criteri di ricerca della finestra."""
from __future__ import annotations

from stillfind.tools.library import SavedSearches


class SearchCriteria:
    """I criteri di ricerca mostrati nella finestra: lettura, registrazione nella cronologia e ripristino."""

    @staticmethod
    def of(app):
        return dict(query=app.qvar.get().strip(), category=app.type_var.get(), where=app.where_var.get(),
                    order=app.order_var.get(), date=app.date_var.get(), custom=app._date_custom,
                    folder=app.folder_filter)

    @staticmethod
    def record(app):
        from stillfind.core.labels import ALL_TYPES, ANY_DATE
        c = SearchCriteria.of(app)
        if c["query"] or c["folder"] or c["category"] != ALL_TYPES or c["date"] != ANY_DATE:
            if app.cfg.get("search_history", True):
                SavedSearches(app.con).save(c)

    @staticmethod
    def restore(app, c):
        from stillfind.core.labels import ALL_FOLDERS
        app.type_var.set(c["category"])
        app.where_var.set(c["where"])
        app.order_var.set(c["order"])
        app.date_var.set(c["date"])
        app._date_custom = c["custom"]
        app.folder_filter = c["folder"]
        label = next((k for k,v in app._folder_map.items() if v == c["folder"]), c["folder"] or ALL_FOLDERS)
        app.folder_var.set(label)
        app.qvar.set(c["query"])
        app.do_search(force=True)
