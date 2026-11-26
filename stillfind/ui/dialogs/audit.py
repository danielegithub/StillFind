# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Mappa dei dati personali."""
from __future__ import annotations

import queue
import threading
from contextlib import closing
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from stillfind.core import facts
from stillfind.core.i18n import tr, trn
from stillfind.storage.database import Database
from stillfind.tools.library import PersonalDataAudit
from stillfind.ui.theme import PAL, font, ghost_button, primary_button
from stillfind.ui.widgets import SquareDialog, SquareEntry, TreeFrame


class AuditDialog(SquareDialog):
    def __init__(self, app):
        super().__init__(app, tr("Mappa dei dati personali"), "980x540", minsize=(760, 380))
        self.app = app
        self.note = ctk.CTkLabel(self, text=tr("Sono considerate le entità abilitate e il testo già indicizzato."),
                                 font=font(12), text_color=PAL["muted"], anchor="w", justify="left", wraplength=900)
        self.note.pack(side="bottom", fill="x", padx=self.MARGIN, pady=(8, self.MARGIN))
        bar = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        bar.pack(fill="x", padx=self.MARGIN, pady=(self.MARGIN, 10))
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bar, text=tr("Identificativo del soggetto"), font=font(13), text_color=PAL["text"]
                     ).grid(row=0, column=0, padx=(0, 10))
        self.query = SquareEntry(bar, height=36, font=font(13))
        self.query.grid(row=0, column=1, sticky="ew")
        self.query.bind("<Return>", lambda _: self.scan())
        primary_button(bar, tr("Cerca nell’archivio"), self.scan).grid(row=0, column=2, padx=(10, 0))
        ghost_button(bar, tr("Desktop e Download"), self.exposure).grid(row=0, column=3, padx=(8, 0))
        ghost_button(bar, tr("Controlla cartella…"), self.folder).grid(row=0, column=4, padx=(8, 0))
        table = TreeFrame(self, [("name", tr("Documento"), 260), ("kind", tr("Tipo"), 140),
                                 ("value", tr("Valore trovato"), 220), ("folder", tr("Cartella"), 300)])
        table.pack(fill="both", expand=True, padx=self.MARGIN)
        self.tree = table.tree
        self.tree.bind("<Double-1>", lambda _: self.open())
        self.tree.bind("<Return>", lambda _: self.open())
        self.items = {}
        self.seq = 0
        self.after(100, self.query.focus_set)
        self.scan()

    def exposure(self):
        folders = PersonalDataAudit.exposure_folders()
        if folders:
            self.scan(folders)
        else:
            self.note.configure(text=tr("Nessuna cartella Desktop o Download disponibile."))

    def folder(self):
        folder = filedialog.askdirectory(parent=self)
        if folder:
            self.scan([folder])

    def scan(self, folders=()):
        result = queue.Queue()
        self.seq += 1
        seq, query = self.seq, self.query.get()
        self.note.configure(text=tr("Ricerca delle menzioni nell’archivio…"))
        def work():
            try:
                with closing(Database.connect()) as con:
                    rows = PersonalDataAudit(con).find(query,folders)
                    issues = con.execute("SELECT count(*) FROM docs WHERE error<>'' "
                                         "OR (ext='pdf' AND (notext=1 OR blank_pages>0))").fetchone()[0]
                    result.put((rows, issues))
            except Exception as exc:
                result.put(str(exc))
        threading.Thread(target=work,daemon=True).start()
        def poll():
            if seq != self.seq:
                return
            try:
                rows = result.get_nowait()
            except queue.Empty:
                self.after(100,poll)
                return
            self.tree.delete(*self.tree.get_children())
            self.items.clear()
            if isinstance(rows,str):
                self.note.configure(text=rows)
                return
            rows, issues = rows
            def display(offset=0):
                if seq != self.seq:
                    return
                for doc,name,path,kind,value,start,count,end in rows[offset:offset+180]:
                    label = facts.entity_label(kind, tr("Testo esatto"))
                    row = self.tree.insert("", "end",values=(name,label,value,Path(path).parent))
                    self.items[row] = doc,start,end
                if offset+180 < len(rows):
                    self.after(1, lambda: display(offset+180))
                else:
                    files = len({row[0] for row in rows})
                    coverage = (" · " + trn("{n} file nell’archivio da verificare in Diagnostica",
                                            "{n} file nell’archivio da verificare in Diagnostica", issues)) if issues else ""
                    self.note.configure(text=trn("{n} file con riscontri nel testo indicizzato",
                                                 "{n} file con riscontri nel testo indicizzato", files)
                                        + coverage + " · " + tr("doppio clic per aprire il passaggio"))
            display()
        self.after(10,poll)

    def open(self):
        sel = self.tree.selection()
        if sel:
            self.app.open_indexed(*self.items[sel[0]])
            self.destroy()
