# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Scelta di un intervallo di date."""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from stillfind.core import i18n, labels as ui_text
from stillfind.core.i18n import tr
from stillfind.ui.theme import PAL, font, ghost_button, primary_button
from stillfind.ui.widgets import SquareDialog, SquareEntry


class DateRangeDialog(SquareDialog):
    """Scelta di un intervallo di date a mano: «dal» e «al», anche una sola. `on_apply(dopo, prima, etichetta)`."""

    def __init__(self, parent, on_apply, start: str = "", end: str = ""):
        super().__init__(parent, tr("Scegli le date"), "480x340", resizable=False, modal=True)
        self._on_apply = on_apply
        bar = self.button_bar(primary_button(self, tr("Applica"), self._apply), ghost_button(self, tr("Annulla"), self.close))
        ctk.CTkLabel(self, text=tr("Documenti modificati in questo periodo"), font=font(17, True),
                     text_color=PAL["text"]).pack(anchor="w", padx=self.MARGIN, pady=(18, 2))
        ctk.CTkLabel(self, text=tr("Scrivi le date come {full}. Basta anche il mese ({month}) o l'anno ({year}): "
                                   "vale tutto il periodo. Puoi lasciare vuota una delle due.",
                               full=i18n.Formats.numeric_date(2025, 12, 31), month=i18n.Formats.numeric_date(2025, 12), year=2025),
                     font=font(12), text_color=PAL["muted"], wraplength=430, justify="left", anchor="w"
                     ).pack(anchor="w", padx=self.MARGIN, pady=(0, 12))
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=self.MARGIN)
        form.grid_columnconfigure(1, weight=1)
        self.entries = []
        for row, (label, value) in enumerate(((tr("Dal"), start), (tr("Al"), end))):
            ctk.CTkLabel(form, text=label, font=font(13), text_color=PAL["text"], width=40, anchor="w"
                         ).grid(row=row, column=0, pady=6)
            e = SquareEntry(form, height=36, font=font(14), placeholder_text=tr("gg/mm/aaaa"))
            e.grid(row=row, column=1, sticky="ew", pady=6)
            if value:
                e.insert(0, value)
            e.bind("<Return>", lambda _e: self._apply())
            self.entries.append(e)
        self.error = ctk.CTkLabel(self, text="", font=font(12), text_color=PAL["err"], wraplength=430,
                                  justify="left", anchor="w")
        self.error.pack(anchor="w", padx=self.MARGIN, pady=(6, 0))
        self.after(200, self._focus)

    def _focus(self):
        try:
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
