# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Mappa dei dati personali."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from contextlib import closing
from pathlib import Path
from tkinter import ttk, filedialog

from stillfind.core import facts
from stillfind.storage.database import Database
from stillfind.tools.library import PersonalDataAudit


class AuditDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Mappa dei dati personali")
        self.geometry("920x480")
        self.transient(app)
        bar = ttk.Frame(self)
        bar.pack(fill="x",padx=10,pady=8)
        ttk.Label(bar,text="Identificativo del soggetto").pack(side="left")
        self.query = ttk.Entry(bar)
        self.query.pack(side="left",padx=5,fill="x",expand=True)
        self.query.bind("<Return>",lambda _: self.scan())
        ttk.Button(bar,text="Cerca nell’archivio",command=self.scan).pack(side="left")
        ttk.Button(bar,text="Desktop e Download",command=self.exposure).pack(side="left")
        ttk.Button(bar,text="Controlla cartella…",command=self.folder).pack(side="left")
        table = ttk.Frame(self)
        table.pack(fill="both",expand=True,padx=10)
        self.tree = ttk.Treeview(table, columns=("name","kind","value","folder"), show="headings")
        for k,t in zip(self.tree["columns"],("Documento","Tipo","Valore trovato","Cartella")):
            self.tree.heading(k,text=t)
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both",expand=True)
        self.tree.bind("<Double-1>", lambda _: self.open())
        self.note = ttk.Label(self,text="Sono considerate le entità abilitate e il testo già indicizzato.")
        self.note.pack(anchor="w",padx=10,pady=5)
        self.items = {}
        self.seq = 0
        self.scan()

    def exposure(self):
        folders = PersonalDataAudit.exposure_folders()
        if folders:
            self.scan(folders)
        else:
            self.note.configure(text="Nessuna cartella Desktop o Download disponibile.")

    def folder(self):
        folder = filedialog.askdirectory(parent=self)
        if folder:
            self.scan([folder])

    def scan(self, folders=()):
        result = queue.Queue()
        self.seq += 1
        seq, query = self.seq, self.query.get()
        self.note.configure(text="Ricerca delle menzioni nell’archivio…")
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
                    label = facts.ENTITY_LABELS.get(kind, "Testo esatto")
                    row = self.tree.insert("", "end",values=(name,label,value,Path(path).parent))
                    self.items[row] = doc,start,end
                if offset+180 < len(rows):
                    self.after(1, lambda: display(offset+180))
                else:
                    files = len({row[0] for row in rows})
                    coverage = f" · {issues} file nell’archivio da verificare in Diagnostica" if issues else ""
                    self.note.configure(text=f"{files} file con riscontri nel testo indicizzato{coverage} · "
                                             "doppio clic per aprire il passaggio")
            display()
        self.after(10,poll)

    def open(self):
        sel = self.tree.selection()
        if sel:
            self.app.open_indexed(*self.items[sel[0]])
            self.destroy()
