# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Ricerca dei duplicati."""
from __future__ import annotations

import queue
import threading

import customtkinter as ctk

from stillfind.core import errors, system
from stillfind.core.i18n import tr, trn
from stillfind.indexing.duplicates import DuplicateFinder
from stillfind.storage.database import Database
from stillfind.ui.theme import PAL, font, ghost_button
from stillfind.ui.widgets import SquareDialog, TreeFrame


class DuplicatesDialog(SquareDialog):
    """Documenti con contenuto identico (SHA-256 completo), dai più ingombranti."""

    def __init__(self, parent):
        super().__init__(parent, tr("Documenti duplicati"), "840x540", minsize=(640, 400))
        self._stop = threading.Event()
        self._q: queue.Queue = queue.Queue()
        self._paths: dict[str, str] = {}
        bar = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        bar.pack(side="bottom", fill="x", padx=self.MARGIN, pady=self.MARGIN)
        ghost_button(bar, tr("Chiudi"), self.close).pack(side="right")
        ctk.CTkLabel(bar, text=tr("Doppio clic su un file per mostrarlo nella sua cartella. Nessun file viene cancellato."),
                     font=font(12), text_color=PAL["muted"], anchor="w", justify="left", wraplength=560
                     ).pack(side="left", fill="x", expand=True)
        self.label = ctk.CTkLabel(self, text=tr("Sto confrontando i file…"), font=font(13, True),
                                  text_color=PAL["text"], anchor="w")
        self.label.pack(fill="x", padx=self.MARGIN, pady=(self.MARGIN, 8))
        box = TreeFrame(self, [("size", tr("Dimensione"), 110)], show="tree headings")
        box.pack(fill="both", expand=True, padx=self.MARGIN)
        self.tree = box.tree
        self.tree.heading("#0", text=tr("File"), anchor="w")
        self.tree.heading("size", text=tr("Dimensione"), anchor="e")
        self.tree.column("#0", width=600, stretch=True)
        self.tree.column("size", width=110, anchor="e", stretch=False)
        self.tree.bind("<Double-1>", self._reveal)
        threading.Thread(target=self._work, daemon=True).start()
        self.after(150, self._poll)

    def _work(self):
        con = Database.connect()  # connessione propria: quella della finestra non si può usare da un altro thread
        try:
            res = DuplicateFinder(con).find(self._stop, lambda i, n: self._q.put(("progress", (i, n))))
            self._q.put(("done", res))
        except errors.Stopped:
            pass
        except Exception as ex:
            self._q.put(("error", str(ex)))
        finally:
            con.close()

    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "progress":
                    self.label.configure(text=tr("Sto confrontando i file… ({done} di {total})", done=data[0] + 1, total=data[1]))
                elif kind == "error":
                    self.label.configure(text=tr("Errore: {error}", error=data))
                    return
                else:
                    self._show(data)
                    return
        except queue.Empty:
            pass
        if self.winfo_exists() and not self._stop.is_set():
            self.after(150, self._poll)

    def _show(self, groups):
        if not groups:
            self.label.configure(text=tr("Non ci sono documenti duplicati."))
            return
        wasted = sum(g["wasted"] for g in groups)
        self.label.configure(text=trn("{n} gruppo di copie identiche · spazio che puoi liberare: {space}",
                                      "{n} gruppi di copie identiche · spazio che puoi liberare: {space}", len(groups),
                                      space=system.human_size(wasted)))
        for g in groups:
            parent = self.tree.insert("", "end", open=True, text=tr("{n} copie identiche", n=len(g["paths"])),
                                      values=(system.human_size(g["size"]),))
            for path in g["paths"]:
                iid = self.tree.insert(parent, "end", text=path, values=(system.human_size(g["size"]),))
                self._paths[iid] = path

    def _reveal(self, _event=None):
        sel = self.tree.selection()
        if sel and sel[0] in self._paths:
            system.reveal_file(self._paths[sel[0]])

    def close(self):
        self._stop.set()
        super().destroy()
