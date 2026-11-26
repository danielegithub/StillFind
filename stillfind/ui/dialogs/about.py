# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Finestra «Informazioni»."""
from __future__ import annotations

import customtkinter as ctk

from stillfind.core import about
from stillfind.core.i18n import tr
from stillfind.ui.theme import PAL, card, font, primary_button
from stillfind.ui.widgets import SquareDialog


class AboutDialog(SquareDialog):
    """Informazioni su StillFind: autore, licenza e assenza di garanzia (GPL-3.0, sezione 5d)."""

    def __init__(self, parent):
        super().__init__(parent, tr("Informazioni su StillFind"), "560x560", resizable=False)
        self.button_bar(primary_button(self, tr("Chiudi"), self.close, width=100))
        box = card(self)
        box.pack(fill="both", expand=True, padx=self.MARGIN, pady=(self.MARGIN, 0))
        ctk.CTkLabel(box, text="StillFind", font=font(24, True), text_color=PAL["text"]
                     ).pack(anchor="w", padx=20, pady=(18, 0))
        ctk.CTkLabel(box, text=tr("Versione {version}", version=about.__version__), font=font(13), text_color=PAL["muted"]
                     ).pack(anchor="w", padx=20)
        ctk.CTkLabel(box, text=about.notice(), font=font(13), text_color=PAL["text"], wraplength=470,
                     justify="left", anchor="w").pack(anchor="w", padx=20, pady=16)
