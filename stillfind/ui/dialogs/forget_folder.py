# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Scelta della cartella da dimenticare."""
from __future__ import annotations

import customtkinter as ctk

from stillfind.core.i18n import tr
from stillfind.ui.theme import PAL, font, ghost_button, primary_button
from stillfind.ui.widgets import SquareComboBox, SquareDialog


class ForgetFolderDialog(SquareDialog):
    """Modale: il risultato (`.result`) è la cartella scelta, oppure None se si annulla."""

    def __init__(self, parent, title=None):
        super().__init__(parent, title or tr("Dimentica una cartella"), "640x250", minsize=(520, 250), resizable=True, modal=True)
        self.result = None
        folders = [row[0] for row in parent.con.execute(
            "SELECT path FROM dirs UNION SELECT folder FROM docs ORDER BY 1 COLLATE NOCASE")]
        confirm = primary_button(self, tr("Dimentica"), self._accept, width=110)
        self.button_bar(confirm, ghost_button(self, tr("Annulla"), self.close, width=100))
        if not folders:
            confirm.configure(state="disabled")
        ctk.CTkLabel(self, text=tr("Cartella da dimenticare"), font=font(13, True), text_color=PAL["text"], anchor="w"
                     ).pack(anchor="w", padx=self.MARGIN, pady=(self.MARGIN, 6))
        self.choice = SquareComboBox(self, values=folders or [""], state="readonly", height=36, font=font(13),
                                     dropdown_font=font(13))
        self.choice.pack(fill="x", padx=self.MARGIN)
        if folders:
            self.choice.set(parent.folder_filter if parent.folder_filter in folders else folders[0])
        else:
            self.choice.set("")
        self.note = ctk.CTkLabel(self, text=tr("La rimozione include le sottocartelle. Gli originali restano sul disco.\n"
                                               "Puoi includere di nuovo la cartella dalle Impostazioni."),
                                 font=font(12), text_color=PAL["muted"], justify="left", anchor="w", wraplength=580)
        self.note.pack(anchor="w", padx=self.MARGIN, pady=(12, 0))
        self.wait_window()

    def _accept(self):
        self.result = self.choice.get() or None
        self.destroy()
