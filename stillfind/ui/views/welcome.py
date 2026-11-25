# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Schermata iniziale."""
from __future__ import annotations

import os
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from stillfind.ui import security_ui
from stillfind.ui.theme import PAL, font, ghost_button, primary_button, card


class WelcomeView(ctk.CTkFrame):
    """Primo avvio: una frase che spiega cosa fa StillFind e un solo passo, scegliere le cartelle."""

    SUGGESTED = (("Documenti", ("Documents", "Documenti"), True),
                 ("Scrivania", ("Desktop", "Scrivania"), True),
                 ("Download", ("Downloads", "Download"), False))

    def __init__(self, app, on_start):
        super().__init__(app, fg_color=PAL["bg"], corner_radius=0)
        self.on_start = on_start
        self.choices: list[tuple[str, ctk.BooleanVar]] = []
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(box, text="🔍", font=font(46)).pack()
        ctk.CTkLabel(box, text="StillFind", font=font(32, True), text_color=PAL["text"]).pack(pady=(2, 0))
        ctk.CTkLabel(box, text="Trova le parole dentro i tuoi documenti:\\nPDF, Word, Excel, email e molto altro.",
                     font=font(15), text_color=PAL["muted"], justify="center").pack(pady=(6, 4))
        ctk.CTkLabel(box, text="🔒  Funziona senza internet: nulla esce dal tuo computer.", font=font(13, True),
                     text_color=PAL["accent"]).pack(pady=(0, 24))
        ctk.CTkLabel(box, text="Dove sono i tuoi documenti?", font=font(15, True), text_color=PAL["text"],
                     anchor="w").pack(fill="x")
        self.list = card(box)
        self.list.pack(fill="x", pady=(8, 8))
        self.list.grid_columnconfigure(0, weight=1)
        for label, names, checked in self.SUGGESTED:
            path = next((str(Path.home() / n) for n in names if (Path.home() / n).is_dir()), None)
            if path:
                self._add(label, path, checked)
        self._placeholder = None
        if not self.choices:
            self._placeholder = ctk.CTkLabel(self.list, text="Scegli la cartella che contiene i tuoi documenti.",
                                             font=font(13), text_color=PAL["muted"])
            self._placeholder.grid(row=0, column=0, padx=16, pady=14)
        ghost_button(box, "＋  Scegli un'altra cartella…", self._choose).pack(anchor="w")
        ghost_button(box, "Protezione dell’archivio…", lambda: security_ui.restart(app, True)).pack(anchor="w", pady=5)
        btn = primary_button(box, "Inizia", self._start)
        btn.configure(height=46, corner_radius=14, font=font(16, True))
        btn.pack(fill="x", pady=(24, 8))
        ctk.CTkLabel(box, text="StillFind legge anche le sottocartelle. Potrai cambiare le cartelle quando vuoi\\n"
                               "dalle Impostazioni. All'inizio serve qualche minuto, ma puoi già cercare.",
                     font=font(12), text_color=PAL["muted"], justify="center").pack()

    def _add(self, label: str, path: str, checked: bool = True):
        if any(p == path for p, _ in self.choices):
            return
        var = ctk.BooleanVar(value=checked)
        self.choices.append((path, var))
        row = len(self.choices) - 1
        if getattr(self, "_placeholder", None) is not None:
            self._placeholder.destroy()
            self._placeholder = None
        ctk.CTkCheckBox(self.list, text=label, variable=var, checkbox_width=22, checkbox_height=22, corner_radius=6,
                        fg_color=PAL["accent"], hover_color=PAL["accent_hover"], font=font(14),
                        text_color=PAL["text"]).grid(row=row, column=0, sticky="w", padx=16, pady=(10, 2))
        ctk.CTkLabel(self.list, text=path, font=font(11),
                     text_color=PAL["muted"], anchor="w").grid(row=row, column=1, sticky="w", padx=(0, 16), pady=(10, 2))

    def _choose(self):
        d = filedialog.askdirectory(parent=self.winfo_toplevel(), title="Scegli una cartella")
        if d:
            self._add(os.path.basename(d.rstrip("/\\\\")) or d, d, True)

    def _start(self):
        paths = [p for p, v in self.choices if v.get()]
        if not paths:
            messagebox.showinfo("StillFind", "Spunta almeno una cartella, oppure scegline una.",
                                parent=self.winfo_toplevel())
            return
        self.on_start(paths)
