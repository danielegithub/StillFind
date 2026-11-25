# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Scelta della cartella da dimenticare."""
from __future__ import annotations

from tkinter import simpledialog, ttk


class ForgetFolderDialog(simpledialog.Dialog):
    def body(self, master):
        folders = [row[0] for row in self.parent.con.execute(
            "SELECT path FROM dirs UNION SELECT folder FROM docs ORDER BY 1 COLLATE NOCASE")]
        ttk.Label(master, text="Cartella da dimenticare").pack(anchor="w")
        self.choice = ttk.Combobox(master, values=folders, state="readonly", width=64)
        self.choice.pack(fill="x", pady=6)
        if folders:
            self.choice.set(self.parent.folder_filter if self.parent.folder_filter in folders else folders[0])
        ttk.Label(master, text="La rimozione include le sottocartelle. Gli originali restano sul disco.\n"
                              "Puoi includere di nuovo la cartella dalle Impostazioni.").pack(anchor="w")
        return self.choice

    def buttonbox(self):
        bar = ttk.Frame(self)
        bar.pack(anchor="e", padx=8, pady=8)
        ttk.Button(bar, text="Annulla", command=self.cancel).pack(side="left", padx=5)
        ttk.Button(bar, text="Dimentica", command=self.ok,
                   state="normal" if self.choice.get() else "disabled").pack(side="left")
        self.bind("<Escape>", self.cancel)

    def apply(self):
        self.result = self.choice.get()
