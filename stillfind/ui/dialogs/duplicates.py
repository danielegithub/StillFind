# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Ricerca dei duplicati."""
from __future__ import annotations

import queue
import threading
from tkinter import ttk
import customtkinter as ctk
from stillfind.core import errors, system
from stillfind.storage.database import Database
from stillfind.indexing.duplicates import DuplicateFinder
from stillfind.ui.theme import PAL, font, ghost_button, card


class DuplicatesDialog(ctk.CTkToplevel):
    """Documenti con contenuto identico (SHA-256 completo), dai più ingombranti."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Documenti duplicati")
        self.geometry("820x520")
        self.minsize(620, 380)
        self.configure(fg_color=PAL["bg"])
        self.transient(parent)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._stop = threading.Event()
        self._q: queue.Queue = queue.Queue()
        self._paths: dict[str, str] = {}
        self.label = ctk.CTkLabel(self, text="Sto confrontando i file…", font=font(13, True),
                                  text_color=PAL["text"], anchor="w")
        self.label.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        box = card(self)
        box.grid(row=1, column=0, sticky="nsew", padx=14)
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(box, columns=("size",), selectmode="browse", style="Cerca.Treeview")
        self.tree.heading("#0", text="File", anchor="w")
        self.tree.heading("size", text="Dimensione", anchor="e")
        self.tree.column("#0", width=600, stretch=True)
        self.tree.column("size", width=110, anchor="e", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        sb = ctk.CTkScrollbar(box, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=4)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", self._reveal)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=14, pady=14)
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="Doppio clic su un file per mostrarlo nella sua cartella. Nessun file viene cancellato.",
                     font=font(12), text_color=PAL["muted"], anchor="w").grid(row=0, column=0, sticky="w")
        ghost_button(bar, "Chiudi", self._close).grid(row=0, column=1)
        self.protocol("WM_DELETE_WINDOW", self._close)
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
                    self.label.configure(text=f"Sto confrontando i file… ({data[0] + 1} di {data[1]})")
                elif kind == "error":
                    self.label.configure(text=f"Errore: {data}")
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
            self.label.configure(text="Non ci sono documenti duplicati.")
            return
        wasted = sum(g["wasted"] for g in groups)
        self.label.configure(text=f"{len(groups)} gruppi di copie identiche · spazio che puoi liberare: {system.human_size(wasted)}")
        for g in groups:
            parent = self.tree.insert("", "end", open=True, text=f"{len(g['paths'])} copie identiche",
                                      values=(system.human_size(g["size"]),))
            for path in g["paths"]:
                iid = self.tree.insert(parent, "end", text=path, values=(system.human_size(g["size"]),))
                self._paths[iid] = path

    def _reveal(self, _event=None):
        sel = self.tree.selection()
        if sel and sel[0] in self._paths:
            system.reveal_file(self._paths[sel[0]])

    def _close(self):
        self._stop.set()
        self.destroy()
