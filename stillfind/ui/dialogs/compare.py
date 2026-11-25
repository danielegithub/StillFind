# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Confronto tra versioni."""
from __future__ import annotations

import customtkinter as ctk

from stillfind.indexing.versions import TextVersions
from stillfind.ui.widgets import read_in_background


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
            return a[0], b[0], list(TextVersions.differences(a[1], b[1]))
        read_in_background(self, work, self._display)

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
