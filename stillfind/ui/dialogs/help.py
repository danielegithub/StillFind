# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Guida alla ricerca."""
from __future__ import annotations

import sys
import customtkinter as ctk
from stillfind.ui.theme import PAL, font, primary_button, card


class HelpDialog(ctk.CTkToplevel):
    """Come cercare: esempi in parole semplici (la sintassi sta qui, non sotto la barra di ricerca)."""

    EXAMPLES = (
        ("contratto affitto", "Documenti che contengono tutte le parole, anche in forme simili (contratti, affitti…)."),
        ('"canone mensile"', "Le parole esatte, una dopo l'altra."),
        ("affitto OR locazione", "Almeno una delle due parole."),
        ("affitto NOT cucina", "Esclude i documenti con quella parola."),
        ("contr*", "Parole che iniziano così (contratto, contratti, contrattuale)."),
        ("name:preventivo", "Cerca solo nel nome del file."),
        ("atto notarile:pdf", "Solo file di quel tipo: la parola, due punti e il tipo (pdf, docx, xlsx…). Vale anche ext:pdf."),
        ("after:2024-01", "Modificati da gennaio 2024 in poi (anche solo l'anno: after:2024)."),
        ("before:2024-12", "Modificati fino a dicembre 2024 compreso."),
        ("in:Fatture", "Solo nelle cartelle con quel nome."),
        ("cf:RSSMRA80A01H501U", "Documenti che citano il codice fiscale. Per la partita IVA usa piva:."),
        ("importo:1200..1500", "Un importo in euro compreso tra i due estremi."),
        ("~450", "Una cifra entro il 5% di 450 euro. Funziona anche importo:~450."),
    )

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Come cercare")
        self.geometry("680x680")
        self.configure(fg_color=PAL["bg"])
        self.transient(parent)
        ctk.CTkLabel(self, text="Come cercare", font=font(20, True), text_color=PAL["text"]
                     ).pack(anchor="w", padx=22, pady=(18, 2))
        ctk.CTkLabel(self, text="Di solito basta scrivere due o tre parole. Per ricerche più precise puoi usare questi trucchi.",
                     font=font(13), text_color=PAL["muted"], wraplength=560, justify="left", anchor="w"
                     ).pack(anchor="w", padx=22, pady=(0, 10))
        box = card(self)
        box.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        for i, (example, text) in enumerate(self.EXAMPLES):
            ctk.CTkLabel(box, text=example, font=ctk.CTkFont(family="Menlo" if sys.platform == "darwin" else "Courier",
                                                            size=13, weight="bold"),
                         text_color=PAL["accent"], anchor="w", width=170).grid(row=i, column=0, sticky="w", padx=(16, 8), pady=7)
            ctk.CTkLabel(box, text=text, font=font(13), text_color=PAL["text"], anchor="w", justify="left",
                         wraplength=380).grid(row=i, column=1, sticky="w", pady=7)
        primary_button(self, "Chiudi", self.destroy).pack(anchor="e", padx=22, pady=(0, 16))
