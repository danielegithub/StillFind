# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Scelta della lingua dell'interfaccia al primo avvio."""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from stillfind.core import i18n
from stillfind.core.i18n import tr
from stillfind.ui.widgets import SquareRadioButton
from stillfind.ui.brand import Brand
from stillfind.ui.theme import PAL, card, font, primary_button


class LanguageView(ctk.CTkFrame):
    """Prima schermata: propone la lingua del sistema (o l'inglese se non è supportata). I testi cambiano subito
    nella lingua evidenziata, così si vede già che cosa si sta scegliendo."""

    def __init__(self, app, on_choose):
        super().__init__(app, fg_color=PAL["bg"], corner_radius=0, border_width=0)
        self.on_choose = on_choose
        self.choice = tk.StringVar(value=i18n.Languages.system())
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        panel = card(self)
        panel.grid(row=0, column=0)
        panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(panel, text="", image=Brand.wordmark(280)).grid(row=0, column=0, padx=48, pady=(30, 6))
        self.heading = ctk.CTkLabel(panel, text="", font=font(16, True), text_color=PAL["text"])
        self.heading.grid(row=1, column=0, padx=48, pady=(0, 16))
        self.radios = []
        for row, (code, name) in enumerate(i18n.Languages.ALL, start=2):
            radio = SquareRadioButton(panel, text=name, variable=self.choice, value=code, command=self._refresh,
                                       font=font(15), text_color=PAL["text"], fg_color=PAL["accent"],
                                       hover_color=PAL["accent_hover"], border_width_checked=6, width=220)
            radio.grid(row=row, column=0, sticky="w", padx=96, pady=6)
            tk.Frame.configure(radio, takefocus=True)
            tk.Misc.bind(radio, "<Return>", lambda e: (self._continue(), "break")[1])
            self.radios.append(radio)
        self.note = ctk.CTkLabel(panel, text="", font=font(12), text_color=PAL["muted"], wraplength=340, justify="center")
        self.note.grid(row=8, column=0, padx=48, pady=(16, 8))
        self.button = primary_button(panel, "", self._continue, width=220)
        self.button.configure(height=42)
        self.button.grid(row=9, column=0, padx=48, pady=(8, 34))
        self._refresh()
        self.after_idle(lambda: tk.Misc.focus_set(self.radios[i18n.Languages.CODES.index(self.choice.get())]))

    def _refresh(self):
        i18n.Translator.set_language(self.choice.get())
        self.heading.configure(text=tr("Scegli la lingua"))
        self.note.configure(text=tr("Potrai cambiarla in Impostazioni → Aspetto e Sistema → Lingua."))
        self.button.configure(text=tr("Continua"))

    def _continue(self):
        self.on_choose(self.choice.get())
