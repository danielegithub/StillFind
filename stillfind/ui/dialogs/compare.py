# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Confronto tra versioni."""
from __future__ import annotations

import customtkinter as ctk

from stillfind.core.i18n import tr
from stillfind.indexing.versions import TextVersions
from stillfind.ui.theme import PAL, font, ghost_button
from stillfind.ui.widgets import SquareDialog, read_in_background


class CompareDialog(SquareDialog):
    def __init__(self, app, left, right):
        super().__init__(app, tr("Confronta versioni"), "980x600", minsize=(720, 420))
        self.hunks, self.index = [], 0
        self.labels, self.boxes = [], []
        bar = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        bar.pack(side="bottom", pady=self.MARGIN)
        self.prev = ghost_button(bar, "‹", lambda: self.show(self.index - 1), width=40)
        self.prev.configure(state="disabled")
        self.prev.pack(side="left")
        self.status = ctk.CTkLabel(bar, text=tr("Confronto i testi…"), font=font(13), text_color=PAL["text"])
        self.status.pack(side="left", padx=14)
        self.next = ghost_button(bar, "›", lambda: self.show(self.index + 1), width=40)
        self.next.configure(state="disabled")
        self.next.pack(side="left")
        sides = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        sides.pack(fill="both", expand=True, padx=self.MARGIN, pady=(self.MARGIN, 0))
        sides.grid_rowconfigure(1, weight=1)
        for i in (0, 1):
            sides.grid_columnconfigure(i, weight=1, uniform="versioni")
            label = ctk.CTkLabel(sides, text="", font=font(13, True), text_color=PAL["text"], anchor="w",
                                 justify="left", wraplength=400)
            label.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 8 if i == 0 else 0), pady=(0, 6))
            box = ctk.CTkTextbox(sides, wrap="word", font=font(13), border_width=1, border_color=PAL["line"])
            box.grid(row=1, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 8 if i == 0 else 0))
            box._textbox.configure(padx=10, pady=8)
            box._textbox.tag_configure("changed", foreground="#161616", background=("#ffc5bd", "#b0e6be")[i])
            box.configure(state="disabled")
            self.labels.append(label)
            self.boxes.append(box)
        def work(con):
            def read(doc):
                return con.execute("SELECT d.name,substr(f.content,coalesce(a.inizio_corpo,0)+1) FROM docs d "
                                   "JOIN fts f ON f.rowid=d.id LEFT JOIN analisi a ON a.doc_id=d.id WHERE d.id=?",
                                   (doc,)).fetchone()
            a, b = read(left), read(right)
            if not a or not b:
                raise ValueError(tr("Il documento non è più nell’archivio."))
            if not a[1].strip() or not b[1].strip():
                raise ValueError(tr("Per confrontare le versioni serve testo leggibile in entrambi i documenti."))
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
            self.status.configure(text=tr("I testi estratti coincidono."))

    def show(self, index):
        self.index = index
        self.status.configure(text=tr("Differenza {i} di {n}", i=index + 1, n=len(self.hunks)))
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
