# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Finestra «Informazioni»."""
from __future__ import annotations

import customtkinter as ctk

from stillfind.core import about
from stillfind.ui.theme import PAL, font, primary_button


class AboutDialog(ctk.CTkToplevel):
    """Informazioni su StillFind: autore, licenza e assenza di garanzia (GPL-3.0, sezione 5d)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Informazioni su StillFind")
        self.geometry("520x420")
        self.resizable(False, False)
        self.configure(fg_color=PAL["bg"])
        self.transient(parent)
        ctk.CTkLabel(self, text="StillFind", font=font(24, True), text_color=PAL["text"]
                     ).pack(anchor="w", padx=24, pady=(22, 0))
        ctk.CTkLabel(self, text=f"Versione {about.__version__}", font=font(13), text_color=PAL["muted"]
                     ).pack(anchor="w", padx=24)
        ctk.CTkLabel(self, text=about.NOTICE, font=font(13), text_color=PAL["text"], wraplength=470,
                     justify="left", anchor="w").pack(anchor="w", padx=24, pady=16)
        primary_button(self, "Chiudi", self.destroy).pack(anchor="e", padx=24, side="bottom", pady=18)
