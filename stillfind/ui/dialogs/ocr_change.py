# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Avviso al cambio delle lingue OCR quando esistono già documenti letti con l'OCR."""
from __future__ import annotations

import customtkinter as ctk

from stillfind.core.i18n import tr

from stillfind.ui.theme import PAL, font, ghost_button, primary_button
from stillfind.ui.widgets import SquareDialog


class OcrLanguageChangeDialog(SquareDialog):
    """Modale. `.result` è «next» (solo prossime letture), «reprocess» (rielabora i documenti già letti)
    oppure None se si chiude la finestra: in quel caso le impostazioni non vengono salvate."""

    NEXT, REPROCESS = "next", "reprocess"

    def __init__(self, parent):
        super().__init__(parent, tr("Lingue OCR"), "560x330", minsize=(480, 300), resizable=True, modal=True)
        self.result = None
        self.next_button = primary_button(self, tr("Solo prossime letture"), lambda: self._choose(self.NEXT), width=190)
        self.reprocess_button = ghost_button(self, tr("Rielabora documenti esistenti"),
                                             lambda: self._choose(self.REPROCESS), width=230)
        self.button_bar(self.next_button, self.reprocess_button)
        ctk.CTkLabel(self, text=tr("Hai cambiato le lingue OCR"), font=font(18, True), text_color=PAL["text"],
                     anchor="w").pack(anchor="w", padx=self.MARGIN, pady=(self.MARGIN, 8))
        for text in (tr("La nuova scelta si applicherà alle prossime letture. "
                        "Il testo dei documenti già elaborati rimarrà invariato."),
                     tr("Vuoi rielaborare anche i documenti già letti con OCR? L’operazione potrebbe richiedere tempo.")):
            ctk.CTkLabel(self, text=text, font=font(13), text_color=PAL["text"], justify="left", anchor="w",
                         wraplength=520).pack(anchor="w", fill="x", padx=self.MARGIN, pady=(0, 10))
        self.after(200, self.next_button.focus_set)
        self.wait_window()

    def _choose(self, result):
        self.result = result
        self.destroy()
