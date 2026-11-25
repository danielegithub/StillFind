# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Pannello Collegati, scheda cronologica, scadenze e confronto affiancato."""
from __future__ import annotations

import queue
import sqlite3
from . import protection
import threading
from contextlib import closing
from datetime import date, timedelta
from tkinter import ttk

import customtkinter as ctk

from . import config, facts, relations, semantic, versions


def _read(widget, work, display):
    # Tk rimane sul thread principale. Le finestre chiuse e le selezioni superate scartano la risposta.
    ticket = object()
    widget._request = ticket
    reply = queue.SimpleQueue()

    def worker():
        try:
            with closing(protection.connection(config.DB_PATH)) as con:
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


def _tree(parent, columns, branch_title=None):
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


def _date(iso):
    return date.fromisoformat(iso).strftime("%d/%m/%Y") if iso else "Data sconosciuta"


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
        self.tree = _tree(self, [("why", "Collegamento", 165)], branch_title="Entità o documento")
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
                    similar = semantic.similar_documents(con, doc_id)
                    message = "Nessun documento simile tra quelli già elaborati."
                except (ValueError, OSError, RuntimeError):
                    message = "I vettori del significato non sono disponibili."
            return (relations.document_entities(con, doc_id), relations.related_documents(con, doc_id),
                    relations.document_versions(con, doc_id), similar, message)

        _read(self, work, self._display)

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
                add("Documenti collegati", name, f"{_date(stamp)} · {reason}", ("document", other, None, None))
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
        self.tree = _tree(self, columns)
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
                return relations.entity_documents(con, entity[0])
            return relations.deadlines(con, today.isoformat() if period != "Tutte" else None,
                                       (today + timedelta(days=30)).isoformat() if period == "Prossimi 30 giorni" else None)
        _read(self, work, self._display)

    def _display(self, data, error):
        if error:
            self.info.configure(text=error)
            return
        for row in data:
            if self.entity:
                doc, name, stamp, why, a, b = row
            else:
                stamp, doc, name, why, a, b = row
            key = self.tree.insert("", "end", values=(_date(stamp), name, why))
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


class CompareDialog(ctk.CTkToplevel):
    def __init__(self, app, left, right):
        super().__init__(app)
        self.title("Confronta versioni")
        self.geometry("960x560")
        self.transient(app)
        self.hunks, self.index = [], 0
        self.labels, self.boxes = [], []
        sides = ctk.CTkFrame(self, fg_color="transparent")
        sides.pack(fill="both", expand=True, padx=10, pady=10)
        sides.grid_rowconfigure(1, weight=1)
        for i in (0, 1):
            sides.grid_columnconfigure(i, weight=1, uniform="versioni")
            label = ctk.CTkLabel(sides, text="", wraplength=370)
            label.grid(row=0, column=i, pady=6)
            box = ctk.CTkTextbox(sides, wrap="word")
            box.grid(row=1, column=i, sticky="nsew", padx=4)
            box._textbox.tag_configure("changed", foreground="#161616", background=("#ffc5bd", "#b0e6be")[i])
            box.configure(state="disabled")
            self.labels.append(label)
            self.boxes.append(box)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(pady=6)
        self.prev = ctk.CTkButton(bar, text="‹", width=40, command=lambda: self.show(self.index-1), state="disabled")
        self.prev.pack(side="left")
        self.status = ctk.CTkLabel(bar, text="Confronto i testi…")
        self.status.pack(side="left", padx=12)
        self.next = ctk.CTkButton(bar, text="›", width=40, command=lambda: self.show(self.index+1), state="disabled")
        self.next.pack(side="left")
        def work(con):
            def read(doc):
                return con.execute("SELECT d.name,substr(f.content,coalesce(a.inizio_corpo,0)+1) FROM docs d "
                                   "JOIN fts f ON f.rowid=d.id LEFT JOIN analisi a ON a.doc_id=d.id WHERE d.id=?",
                                   (doc,)).fetchone()
            a, b = read(left), read(right)
            if not a or not b:
                raise ValueError("Il documento non è più nell’archivio.")
            if not a[1].strip() or not b[1].strip():
                raise ValueError("Per confrontare le versioni serve testo leggibile in entrambi i documenti.")
            return a[0], b[0], list(versions.differences(a[1], b[1]))
        _read(self, work, self._display)

    def _display(self, data, error):
        if error:
            self.status.configure(text=error)
            return
        left, right, self.hunks = data
        for label, name in zip(self.labels, (left, right)):
            label.configure(text=name)
        if self.hunks:
            self.show(0)
        else:
            self.status.configure(text="I testi estratti coincidono.")

    def show(self, index):
        self.index = index
        self.status.configure(text=f"Differenza {index + 1} di {len(self.hunks)}")
        self.prev.configure(state="normal" if index else "disabled")
        self.next.configure(state="normal" if index + 1 < len(self.hunks) else "disabled")
        for box, (text, spans) in zip(self.boxes, self.hunks[index]):
            box.configure(state="normal")
            box.delete("1.0", "end")
            pos = 0
            for a, b in spans:
                box.insert("end", text[pos:a])
                box.insert("end", text[a:b], "changed")
                pos = b
            box.insert("end", text[pos:])
            box.configure(state="disabled")
