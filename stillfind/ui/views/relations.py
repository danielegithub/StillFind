# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Collegamenti, versioni e scadenze del documento selezionato."""
from __future__ import annotations

import customtkinter as ctk
from stillfind.core import facts
from stillfind.indexing.relations import RelationGraph
from stillfind.semantic.search import SemanticSearch
from stillfind.ui.widgets import read_in_background, make_tree, short_date


class RelationsPane(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.key = None
        self._request = None
        self.actions = {}
        self.version_ids = []
        self.info = ctk.CTkLabel(self, text="", anchor="w")
        self.info.pack(fill="x", padx=6)
        self.tree = make_tree(self, [("why", "Collegamento", 165)], branch_title="Entità o documento")
        self.tree.bind("<Double-1>", lambda _: self.open())
        self.tree.bind("<Return>", lambda _: self.open())
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", pady=5)
        self.btn_open = ctk.CTkButton(bar, text="Apri", width=70, command=self.open)
        self.btn_open.pack(side="left", padx=4)
        self.btn_diff = ctk.CTkButton(bar, text="Confronta versioni", width=126, command=self.compare)
        self.btn_diff.pack(side="left", padx=4)

    def reset(self):
        self.key = self._request = None
        self.actions.clear()
        self.version_ids.clear()
        self.tree.delete(*self.tree.get_children())
        self.info.configure(text="")
        self.btn_open.configure(state="disabled")
        self.btn_diff.configure(state="disabled")

    def load(self, doc_id, meanings):
        if self.key == (doc_id, meanings):
            return
        self.reset()
        self.key = doc_id, meanings
        self.info.configure(text="Carico i collegamenti…")

        def work(con):
            similar, message = [], ""
            if meanings:
                try:
                    similar = SemanticSearch(con).similar_documents(doc_id)
                    message = "Nessun documento simile tra quelli già elaborati."
                except (ValueError, OSError, RuntimeError):
                    message = "I vettori del significato non sono disponibili."
            return ((graph := RelationGraph(con)).document_entities(doc_id), graph.related_documents(doc_id),
                    graph.document_versions(doc_id), similar, message)

        read_in_background(self, work, self._display)

    def _display(self, data, error):
        if error:
            self.info.configure(text=error)
            return
        entities, related, variants, similar, message = data
        groups = {}

        def group(label):
            if label not in groups:
                groups[label] = self.tree.insert("", "end", text=label, open=True)
            return groups[label]

        def add(section, name, why, action):
            key = self.tree.insert(group(section), "end", text=name, values=(why,))
            self.actions[key] = action
        for eid, kind, label, offset, roles in entities:
            add("Entità", label, roles or facts.ENTITY_LABELS[kind], ("entity", eid, label))
        for other, name, stamp, source, reason in related:
            if reason != "versione":
                add("Documenti collegati", name, f"{short_date(stamp)} · {reason}", ("document", other, None, None))
        for other, name, score in variants:
            self.version_ids.append(other)
            add("Versioni", name, f"Testo simile al {score:.0%}", ("version", other))
        for row in similar:
            add("Documenti simili", row["name"], f"Documento simile · {row['score']:.0%}",
                ("document", row["id"], row["offset"], row["end"]))
        if message and not similar:
            self.tree.insert(group("Documenti simili"), "end", text=message)
        self.info.configure(text="" if self.actions else "Nessun collegamento trovato per questo documento.")
        if self.actions:
            self.tree.selection_set(next(iter(self.actions)))
            self.btn_open.configure(state="normal")
        self.btn_diff.configure(state="normal" if variants else "disabled")

    def selected(self):
        return self.actions.get(next(iter(self.tree.selection()), ""))

    def open(self):
        action = self.selected()
        if action:
            if action[0] == "entity":
                DocumentList(self.app, entity=(action[1], action[2]))
            elif action[0] == "version":
                self.compare()
            else:
                self.app.open_indexed(*action[1:])

    def compare(self):
        if not self.version_ids or not self.key:
            return
        selected = self.selected()
        other = selected[1] if selected and selected[0] in ("version", "document") else None
        CompareDialog(self.app, self.key[0], other if other in self.version_ids else self.version_ids[0])
