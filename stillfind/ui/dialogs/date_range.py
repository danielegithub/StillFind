# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Scelta di un intervallo di date."""
from __future__ import annotations

import tkinter as tk
import customtkinter as ctk
from stillfind.core import labels as ui_text
from stillfind.ui.theme import PAL, font, ghost_button, primary_button, card


class DateRangeDialog(ctk.CTkToplevel):
    """Scelta di un intervallo di date a mano: «dal» e «al», anche una sola. `on_apply(dopo, prima, etichetta)`."""

    def __init__(self, parent, on_apply, start: str = "", end: str = ""):
        super().__init__(parent)
        self.title("Scegli le date")
        self.geometry("460x330")
        self.resizable(False, False)
        self.configure(fg_color=PAL["bg"])
        self.transient(parent)
        self._on_apply = on_apply
        ctk.CTkLabel(self, text="Documenti modificati in questo periodo", font=font(17, True),
                     text_color=PAL["text"]).pack(anchor="w", padx=22, pady=(18, 2))
        ctk.CTkLabel(self, text="Scrivi le date come 31/12/2025. Basta anche il mese (12/2025) o l'anno (2025): "
                                "vale tutto il periodo. Puoi lasciare vuota una delle due.",
                     font=font(12), text_color=PAL["muted"], wraplength=410, justify="left", anchor="w"
                     ).pack(anchor="w", padx=22, pady=(0, 12))
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=22)
        form.grid_columnconfigure(1, weight=1)
        self.entries = []
        for row, (label, value) in enumerate((("Dal", start), ("Al", end))):
            ctk.CTkLabel(form, text=label, font=font(13), text_color=PAL["text"], width=40, anchor="w"
                         ).grid(row=row, column=0, pady=6)
            e = ctk.CTkEntry(form, height=36, corner_radius=10, font=font(14), placeholder_text="gg/mm/aaaa",
                             fg_color=PAL["card"], border_color=PAL["line"], text_color=PAL["text"])
            e.grid(row=row, column=1, sticky="ew", pady=6)
            if value:
                e.insert(0, value)
            e.bind("<Return>", lambda _e: self._apply())
            self.entries.append(e)
        self.error = ctk.CTkLabel(self, text="", font=font(12), text_color=PAL["err"], wraplength=410,
                                  justify="left", anchor="w")
        self.error.pack(anchor="w", padx=22, pady=(6, 0))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=22, pady=16)
        primary_button(bar, "Applica", self._apply).pack(side="right")
        ghost_button(bar, "Annulla", self.destroy).pack(side="right", padx=8)
        self.after(150, self._focus)

    def _focus(self):
        try:
            self.grab_set()
            self.entries[0].focus_set()
        except tk.TclError:
            pass

    def _apply(self):
        start, end = self.entries[0].get().strip(), self.entries[1].get().strip()  # prima di chiudere: dopo i campi non esistono più
        try:
            after, before, label = ui_text.parse_date_range(start, end)
        except ValueError as ex:
            self.error.configure(text=str(ex))
            return
        self.destroy()
        self._on_apply(after, before, label, start, end)
