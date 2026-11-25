# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Diagnostica dell'indice."""
from __future__ import annotations

from typing import TYPE_CHECKING

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
from stillfind.core import config, errors, system
from stillfind.ui.theme import PAL, font, ghost_button, primary_button, card
from stillfind.ui.dialogs.duplicates import DuplicatesDialog

if TYPE_CHECKING:
    from stillfind.ui.app import App


class DiagnosticsDialog(ctk.CTkToplevel):
    """Stato dell'archivio: file che non si sono potuti leggere e PDF senza testo."""

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title("Stato dell'archivio")
        self.geometry("760x560")
        self.minsize(600, 440)
        self.configure(fg_color=PAL["bg"])
        self.transient(app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        info = app.con.diagnostics()
        self._err_paths: dict[str, str] = {}
        self._pdf_paths: dict[str, str] = {}

        # --- riepilogo
        top = card(self)
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))
        summary = (
            f"Documenti: {info['total']:,}   ·   Letti da scansione: {info['ocr']:,}   ·   "
            f"Senza testo: {info['notext']:,}   ·   Non letti: {info['errors']:,}   ·   "
            f"Spazio occupato: {system.human_size(info['db_bytes'])}"
        ).replace(",", ".")
        ctk.CTkLabel(top, text=summary, font=font(13, True), text_color=PAL["text"], anchor="w"
                     ).pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(top, text=f"Archivio: {config.DB_PATH}", font=font(11), text_color=PAL["muted"], anchor="w"
                     ).pack(fill="x", padx=14, pady=(0, 10))

        # --- schede
        tabs = ctk.CTkTabview(self, fg_color=PAL["card"], segmented_button_selected_color=PAL["accent"],
                              segmented_button_selected_hover_color=PAL["accent_hover"])
        tabs.grid(row=1, column=0, sticky="nsew", padx=14)
        err_tab = tabs.add(f"File non letti ({len(info['error_docs'])})")
        pdf_tab = tabs.add(f"PDF senza testo ({len(info['notext_pdfs'])})")

        self._build_list(
            err_tab, ("name", "error", "folder"),
            (("name", "File", 190), ("error", "Motivo", 270), ("folder", "Cartella", 220)),
            [(d["path"], (d["name"], d["error"], d["folder"])) for d in info["error_docs"]],
            "Tutti i file sono stati letti.", self._err_paths,
        )
        self._build_list(
            pdf_tab, ("name", "size", "ocr", "folder"),
            (("name", "File", 220), ("size", "Dim.", 80), ("ocr", "Scansione", 80), ("folder", "Cartella", 300)),
            [(d["path"], (d["name"], system.human_size(d["size"]), "Sì" if d["ocr"] else "No", d["folder"]))
             for d in info["notext_pdfs"]],
            "Nessun PDF privo di testo.", self._pdf_paths,
        )
        single = ctk.CTkFrame(top, fg_color="transparent")
        single.pack(fill="x", padx=14, pady=(0, 8))
        ghost_button(single, "Rileggi un file…", lambda: self._one()).pack(side="left")
        ghost_button(single, "Esegui OCR su un file…", lambda: self._one(ocr=True)).pack(side="left", padx=6)

        # --- pulsanti
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=14, pady=14)
        bar.grid_columnconfigure(1, weight=1)
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0)
        ghost_button(left, "Rileggi tutto da capo", self._rebuild).pack(side="left")
        ghost_button(left, "Trova duplicati…", lambda: DuplicatesDialog(self)).pack(side="left", padx=(8, 0))
        if info["errors"]:
            primary_button(bar, "Riprova i file non letti", self._retry).grid(row=0, column=2, padx=(0, 8))
        ghost_button(bar, "Chiudi", self.destroy).grid(row=0, column=3)
        self.after(120, self._grab)

    def _grab(self):
        try:
            self.lift()
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass

    def _build_list(self, parent, cols, specs, items, empty_msg, path_map):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        if not items:
            ctk.CTkLabel(parent, text=empty_msg, font=font(13), text_color=PAL["muted"]
                         ).grid(row=0, column=0, pady=24)
            return
        tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="browse", style="Cerca.Treeview")
        for c, label, w in specs:
            tree.heading(c, text=label, anchor="w")
            tree.column(c, width=w, anchor="w", stretch=True)
        for p, vals in items:
            iid = tree.insert("", "end", values=vals)
            path_map[iid] = p
        tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        sb = ctk.CTkScrollbar(parent, command=tree.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=4)
        tree.configure(yscrollcommand=sb.set)
        tree.bind("<Double-1>", lambda e, t=tree, m=path_map: self._reveal(t, m))
        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="w")
        for label, ocr in (("Rileggi selezione", False), ("Esegui OCR sulla selezione", True)):
            ghost_button(actions, label, lambda flag=ocr: self._one(
                path_map.get(tree.focus()), flag, choose=False)).pack(side="left", padx=4)

    def _one(self, path=None, ocr=False, choose=True):
        if choose and not path:
            path = filedialog.askopenfilename(parent=self, title="Scegli un documento già indicizzato")
        if not path:
            return
        if not self.app.con.execute("SELECT 1 FROM docs WHERE path=?", (path,)).fetchone():
            messagebox.showinfo("Diagnostica", "Il file scelto non è presente nell’indice.", parent=self)
            return
        self.destroy()
        self.app.start_index(force=True, target=path, force_ocr=ocr)

    def _reveal(self, tree, path_map):
        sel = tree.selection()
        if sel and sel[0] in path_map:
            system.reveal_file(path_map[sel[0]])

    def _retry(self):
        self.destroy()
        self.app.start_index(force=True, retry_errors=True)

    def _rebuild(self):
        if not messagebox.askyesno(
            "Rileggi tutto da capo",
            "Vuoi svuotare l'archivio e rileggere tutti i documenti da capo?\n"
            "(Per i PDF scansionati la lettura delle immagini verrà ripetuta.)",
            parent=self,
        ):
            return
        self.destroy()
        self.app.rebuild_index()


# --------------------------------------------------------------------------
