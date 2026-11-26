# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Configurazione iniziale guidata: protezione, documenti, OCR, ricerca per significato, conferma."""
from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

from stillfind.core import i18n

from stillfind.core.ocr_languages import OcrLanguages
from stillfind.core.i18n import mark, tr, trn
from stillfind.semantic.models import ModelCatalog
from stillfind.semantic.search import SemanticIndex
from stillfind.ui.brand import Brand
from stillfind.ui.theme import PAL, card, fit_width, font, ghost_button, primary_button
from stillfind.ui.widgets import SquareCheckBox, SquareEntry, SquareRadioButton

class SetupView(ctk.CTkFrame):
    """Percorso breve prima della prima indicizzazione. `on_done(risultato)` riceve le scelte; `on_protection` è
    chiamato con (modalità, passphrase, minuti) prima di concludere e può rifiutare sollevando un errore."""

    STEPS = ("protection", "documents", "ocr", "meaning", "confirm")
    MODES = (("plain", mark("Non cifrare")), ("auto", mark("Portachiavi dell’account")),
             ("vault", mark("Cassaforte con passphrase")))
    TESSERACT_OF = {"it": "ita", "en": "eng", "fr": "fra", "es": "spa", "de": "deu", "pt": "por"}  # interfaccia → OCR
    SUGGESTED = ((mark("Documenti"), ("Documents", "Documenti", "Documentos", "Dokumente"), True),
                 (mark("Scrivania"), ("Desktop", "Scrivania", "Escritorio", "Schreibtisch", "Bureau",
                                      "Área de Trabalho"), True),
                 (mark("Download"), ("Downloads", "Download", "Descargas", "Téléchargements", "Transferências"), False))

    def __init__(self, app, on_done, protection_mode: str = "plain"):
        super().__init__(app, fg_color=PAL["bg"], corner_radius=0, border_width=0)
        self.app, self.on_done, self.current_protection = app, on_done, protection_mode
        self.index = 0
        self.choices: list[tuple[str, ctk.BooleanVar]] = []
        self.mode = tk.StringVar(value="plain")
        self.minutes = tk.StringVar(value="10")
        self.phrase_var, self.repeat_var = tk.StringVar(), tk.StringVar()  # restano fra un passo e l'altro
        self.subfolders = tk.BooleanVar(value=True)
        self.auto_scan = tk.BooleanVar(value=True)
        self.ocr = tk.BooleanVar(value=False)
        self.ocr_vars: dict[str, tk.BooleanVar] = {}
        self.meaning = tk.StringVar(value="off")
        self.meaning_ready = ModelCatalog.is_installed() and SemanticIndex.libraries_available()
        ui = self.TESSERACT_OF.get(i18n.Translator.language(), "eng")
        initial = OcrLanguages.parse([ui, "eng"])
        for code, _ in OcrLanguages.LANGUAGES:
            self.ocr_vars[code] = tk.BooleanVar(value=code in initial)
        self._wrapped: list[ctk.CTkLabel] = []
        self._path_labels: list[ctk.CTkLabel] = []
        self.checks: list = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color=PAL["card"], corner_radius=0, border_width=1, border_color=PAL["line"])
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(header, text="", image=Brand.wordmark(150)).grid(row=0, column=0, padx=24, pady=10)
        self.progress = ctk.CTkLabel(header, text="", font=font(11, True), text_color=PAL["muted"])
        self.progress.grid(row=0, column=1, sticky="e", padx=24)
        self.stage = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        self.stage.grid(row=1, column=0, sticky="nsew", padx=40, pady=20)
        self.stage.grid_columnconfigure(0, weight=1)
        self.stage.bind("<Configure>", self._rewrap)
        bar = ctk.CTkFrame(self, fg_color=PAL["card"], corner_radius=0, border_width=1, border_color=PAL["line"])
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)
        self.back_button = ghost_button(bar, tr("Indietro"), self.back, width=110)
        self.back_button.grid(row=0, column=0, padx=24, pady=14)
        self.error = ctk.CTkLabel(bar, text="", font=font(12), text_color=PAL["err"], wraplength=420, justify="left")
        self.error.grid(row=0, column=1, sticky="w")
        self.next_button = primary_button(bar, tr("Avanti"), self.next, width=190)
        self.next_button.configure(height=40)
        self.next_button.grid(row=0, column=2, padx=24, pady=14)
        for button in (self.back_button, self.next_button):
            tk.Frame.configure(button, takefocus=True)
            for key in ("<space>", "<Return>"):
                tk.Misc.bind(button, key, lambda e, b=button: (b.invoke(), "break")[1])
        self._show(0)

    # ---- struttura
    def focus_first(self):
        if self.STEPS[self.index] == "documents" and self.list.winfo_exists():
            self.list._parent_canvas.yview_moveto(0)
        tk.Misc.focus_set(self.checks[0] if self.checks else self.next_button)

    def _rewrap(self, event=None):
        width = max(260, self.stage.winfo_width() - 40)
        for label in self._wrapped:
            if label.winfo_exists():
                label.configure(wraplength=width)
        for label in self._path_labels:
            if label.winfo_exists():
                label.configure(wraplength=max(200, width - 60))

    def _text(self, parent, text, *, size=13, bold=False, muted=False, **grid):
        label = ctk.CTkLabel(parent, text=text, font=font(size, bold), anchor="w", justify="left",
                             text_color=PAL["muted" if muted else "text"], wraplength=600)
        label.grid(sticky="w", **grid)
        self._wrapped.append(label)
        return label

    def _show(self, index: int):
        self.index = index
        self.error.configure(text="")
        self._wrapped.clear()
        self._path_labels.clear()
        self.checks = []
        for child in self.stage.winfo_children():
            child.destroy()
        title, builder = {"protection": (mark("Protezione"), self._protection), "documents": (mark("Documenti"), self._documents),
                          "ocr": (mark("OCR"), self._ocr), "meaning": (mark("Ricerca per significato"), self._meaning),
                          "confirm": (mark("Conferma"), self._confirm)}[self.STEPS[index]]
        self.progress.configure(text=tr("PRIMO AVVIO  /  PASSO {i} DI {n}  /  {title}", i=index + 1, n=len(self.STEPS),
                                        title=tr(title).upper()))
        builder(self.stage)
        last = index == len(self.STEPS) - 1
        self.next_button.configure(text=tr("Avvia indicizzazione") if last else tr("Avanti"),
                                   width=fit_width(tr("Avvia indicizzazione") if last else tr("Avanti"), 190, True))
        if index == 0:
            self.back_button.grid_remove()
        else:
            self.back_button.grid()
        self.after_idle(self._rewrap)
        self.after_idle(self.focus_first)

    def next(self):
        problem = self._validate()
        if problem:
            self.error.configure(text=problem)
            return
        if self.index == len(self.STEPS) - 1:
            self.on_done(self.result())
        else:
            self._show(self.index + 1)

    def back(self):
        if self.index:
            self._show(self.index - 1)

    # ---- passo 1: protezione
    def _protection(self, parent):
        self._text(parent, tr("Vuoi proteggere l’archivio con la cifratura?"), size=22, bold=True, row=0, column=0)
        self._text(parent, tr("Viene cifrato l’archivio locale di StillFind (testo estratto, ricerche salvate e indice). "
                              "I documenti originali non vengono modificati."), muted=True, row=1, column=0, pady=(6, 14))
        if self.current_protection != "plain":
            self._text(parent, tr("L’archivio è già protetto: {mode}. Potrai cambiare la protezione dalle Impostazioni.",
                                  mode=tr({"auto": mark("Portachiavi dell’account"),
                                           "vault": mark("Cassaforte con passphrase")}[self.current_protection])),
                       row=2, column=0)
            return
        box = card(parent)
        box.grid(row=2, column=0, sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        notes = {"plain": mark("Nessuna cifratura: l’archivio è leggibile da chi ha accesso al disco."),
                 "auto": mark("La chiave è custodita dall’account del computer. Non c’è nulla da ricordare, ma se cambi "
                              "computer o account non potrai aprire l’archivio: dovrai ricostruirlo dai documenti."),
                 "vault": mark("Scegli una passphrase: viene richiesta a ogni apertura e l’archivio si blocca per "
                               "inattività. Non esiste un modo per recuperarla: se la perdi dovrai ricostruire "
                               "l’archivio dai documenti originali.")}
        for row, (key, label) in enumerate(self.MODES):
            SquareRadioButton(box, text=tr(label), variable=self.mode, value=key, font=font(14, True),
                              command=self._mode_changed).grid(row=row * 2, column=0, sticky="w", padx=18, pady=(14, 2))
            note = ctk.CTkLabel(box, text=tr(notes[key]), font=font(12), text_color=PAL["muted"], anchor="w",
                                justify="left", wraplength=560)
            note.grid(row=row * 2 + 1, column=0, sticky="w", padx=(52, 18))
            self._wrapped.append(note)
        self.vault_box = ctk.CTkFrame(box, fg_color="transparent", corner_radius=0)
        self.vault_box.grid(row=7, column=0, sticky="w", padx=(52, 18), pady=(10, 4))
        self.phrase = SquareEntry(self.vault_box, width=300, height=34, show="•", textvariable=self.phrase_var)
        self.repeat = SquareEntry(self.vault_box, width=300, height=34, show="•", textvariable=self.repeat_var)
        self.idle = SquareEntry(self.vault_box, width=90, height=34, textvariable=self.minutes)
        ctk.CTkLabel(self.vault_box, text=tr("Passphrase"), font=font(12), text_color=PAL["muted"]).grid(
            row=0, column=0, sticky="w")
        self.phrase.grid(row=1, column=0, pady=(0, 6), sticky="w")
        ctk.CTkLabel(self.vault_box, text=tr("Ripeti la passphrase"), font=font(12), text_color=PAL["muted"]).grid(
            row=2, column=0, sticky="w")
        self.repeat.grid(row=3, column=0, pady=(0, 6), sticky="w")
        ctk.CTkLabel(self.vault_box, text=tr("Minuti di inattività prima del blocco"), font=font(12),
                     text_color=PAL["muted"]).grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.idle.grid(row=5, column=0, pady=4, sticky="w")
        self._text(box, tr("Cambiare la cifratura in seguito può richiedere una conversione dell’archivio."),
                   size=12, muted=True, row=8, column=0, padx=18, pady=(8, 14))
        self._mode_changed()

    def _mode_changed(self):
        if hasattr(self, "vault_box") and self.vault_box.winfo_exists():
            if self.mode.get() == "vault":
                self.vault_box.grid()
            else:
                self.vault_box.grid_remove()

    # ---- passo 2: documenti
    def _documents(self, parent):
        self._text(parent, tr("Dove sono i tuoi documenti?"), size=22, bold=True, row=0, column=0)
        self._text(parent, tr("Scegli le cartelle da leggere. I documenti originali restano dove sono."), muted=True,
                   row=1, column=0, pady=(6, 12))
        parent.grid_rowconfigure(2, weight=1)
        self.list = ctk.CTkScrollableFrame(parent, corner_radius=0, border_width=1, border_color=PAL["line"],
                                           fg_color=PAL["card"], height=150)
        self.list.grid(row=2, column=0, sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)
        previous = list(self.choices)
        self.choices = []
        self._rows = 0
        self._placeholder = None
        if previous:
            for path, var in previous:
                self._add(os.path.basename(path.rstrip("/\\")) or path, path, var.get())
        else:
            for label, names, checked in self.SUGGESTED:
                path = next((str(Path.home() / n) for n in names if (Path.home() / n).is_dir()), None)
                if path:
                    self._add(tr(label), path, checked)
        if not self.choices:
            self._placeholder = ctk.CTkLabel(self.list, text=tr("Scegli una cartella per iniziare."), font=font(13),
                                             text_color=PAL["muted"])
            self._placeholder.grid(row=0, column=0, padx=12, pady=16)
        self.choose_button = ghost_button(parent, tr("Aggiungi cartella…"), self._choose)
        self.choose_button.grid(row=3, column=0, sticky="w", pady=(10, 6))
        SquareCheckBox(parent, text=tr("Includi anche le sottocartelle"), variable=self.subfolders, font=font(13),
                       checkbox_width=20, checkbox_height=20, corner_radius=0, border_width=1).grid(
            row=4, column=0, sticky="w", pady=(6, 2))
        SquareCheckBox(parent, text=tr("Controlla automaticamente i file nuovi o modificati"), variable=self.auto_scan,
                       font=font(13), checkbox_width=20, checkbox_height=20, corner_radius=0, border_width=1).grid(
            row=5, column=0, sticky="w", pady=(6, 2))

    def _add(self, label, path, checked=True):
        if any(p == path for p, _ in self.choices):
            return
        var = tk.BooleanVar(value=checked)
        self.choices.append((path, var))
        if self._placeholder is not None:
            self._placeholder.destroy()
            self._placeholder = None
        row = 2 * (len(self.choices) - 1)
        check = SquareCheckBox(self.list, text=label, variable=var, checkbox_width=20, checkbox_height=20,
                               corner_radius=0, border_width=1, font=font(13, True))
        check.grid(row=row, column=0, sticky="w", padx=12, pady=(12, 0))
        self.checks.append(check)
        shown = ctk.CTkLabel(self.list, text=path, font=font(11), text_color=PAL["muted"], anchor="w",
                             justify="left", wraplength=500)
        shown.grid(row=row + 1, column=0, sticky="ew", padx=(46, 12), pady=(0, 10))
        self._path_labels.append(shown)

    def _choose(self):
        folder = filedialog.askdirectory(parent=self.winfo_toplevel(), title=tr("Scegli una cartella"))
        if folder:
            self._add(os.path.basename(folder.rstrip("/\\")) or folder, folder, True)

    # ---- passo 3: OCR
    def _ocr(self, parent):
        self._text(parent, tr("Vuoi leggere anche scansioni e fotografie?"), size=22, bold=True, row=0, column=0)
        self._text(parent, tr("Permette di cercare anche dentro scansioni e fotografie. Può richiedere più tempo."),
                   muted=True, row=1, column=0, pady=(6, 14))
        box = card(parent)
        box.grid(row=2, column=0, sticky="ew")
        SquareCheckBox(box, text=tr("Leggi il testo dai PDF scansionati e dalle immagini"), variable=self.ocr,
                       font=font(14, True), checkbox_width=22, checkbox_height=22, corner_radius=0, border_width=1,
                       command=self._ocr_changed).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 8))
        self.ocr_box = ctk.CTkFrame(box, fg_color="transparent", corner_radius=0)
        self.ocr_box.grid(row=1, column=0, sticky="w", padx=(18, 18), pady=(0, 16))
        ctk.CTkLabel(self.ocr_box, text=tr("In quali lingue sono scritti i documenti?"), font=font(12),
                     text_color=PAL["muted"]).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        for i, (code, _) in enumerate(OcrLanguages.LANGUAGES):
            SquareCheckBox(self.ocr_box, text=OcrLanguages.name(code), variable=self.ocr_vars[code], font=font(13),
                           checkbox_width=20, checkbox_height=20, corner_radius=0, border_width=1).grid(
                row=1 + i // 2, column=i % 2, sticky="w", padx=(0, 40), pady=4)
        self._ocr_changed()

    def _ocr_changed(self):
        if self.ocr.get():
            self.ocr_box.grid()
        else:
            self.ocr_box.grid_remove()

    # ---- passo 4: ricerca per significato
    def _meaning(self, parent):
        self._text(parent, tr("Vuoi attivare la ricerca per significato?"), size=22, bold=True, row=0, column=0)
        self._text(parent, tr("Cerca usando le parole presenti nei documenti oppure anche concetti simili. "
                              "Tutto viene elaborato sul tuo computer."), muted=True, row=1, column=0, pady=(6, 14))
        box = card(parent)
        box.grid(row=2, column=0, sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        options = (("off", tr("Disattivata — Solo parole chiave"),
                    tr("Cercando “riparazione automobile”, vengono cercate quelle parole e le varianti riconosciute. "
                       "Un documento che parla soltanto di “intervento del meccanico sulla vettura” potrebbe non comparire.")),
                   ("on", tr("Attivata — Parole chiave e significato"),
                    tr("La stessa ricerca può trovare anche “intervento del meccanico sulla vettura”, perché esprime "
                       "un concetto simile.")))
        for row, (key, title, example) in enumerate(options):
            radio = SquareRadioButton(box, text=title, variable=self.meaning, value=key, font=font(14, True),
                                      state="normal" if key == "off" or self.meaning_ready else "disabled")
            radio.grid(row=row * 2, column=0, sticky="w", padx=18, pady=(14, 2))
            self.checks.append(radio)
            note = ctk.CTkLabel(box, text=example, font=font(12), text_color=PAL["muted"], anchor="w", justify="left",
                                wraplength=560)
            note.grid(row=row * 2 + 1, column=0, sticky="w", padx=(52, 18), pady=(0, 6))
            self._wrapped.append(note)
        extra = tr("L’attivazione richiede tempo e spazio aggiuntivi per preparare l’indice. Il modello è incluso "
                   "nell’app e puoi cambiare la scelta in qualsiasi momento dalle Impostazioni.")
        if not self.meaning_ready:
            extra = tr("Questa installazione non include il modello: la ricerca per significato non è disponibile.")
        self._text(box, extra, size=12, muted=True, row=4, column=0, padx=18, pady=(10, 16))

    # ---- passo 5: conferma
    def _confirm(self, parent):
        self._text(parent, tr("Tutto pronto"), size=22, bold=True, row=0, column=0)
        self._text(parent, tr("Controlla il riepilogo. Potrai cambiare ogni scelta dalle Impostazioni."), muted=True,
                   row=1, column=0, pady=(6, 14))
        box = card(parent)
        box.grid(row=2, column=0, sticky="ew")
        box.grid_columnconfigure(1, weight=1)
        r = self.result()
        mode = r["protection"]["mode"] if self.current_protection == "plain" else self.current_protection
        folders = len(r["folders"])
        langs = OcrLanguages.describe(r["ocr_lang"])
        rows = ((tr("Protezione"), tr(dict(self.MODES).get(mode, mark("Non cifrare")))),
                (tr("Documenti"), trn("{n} cartella", "{n} cartelle", folders) + " · " +
                 (tr("con sottocartelle") if self.subfolders.get() else tr("senza sottocartelle")) + " · " +
                 (tr("controllo automatico") if r["auto_scan"] else tr("controllo manuale"))),
                (tr("OCR"), (tr("attivo ({langs})", langs=langs) if r["ocr"] else tr("spento"))),
                (tr("Ricerca per significato"), tr("parole chiave e significato") if r["semantic"] else tr("solo parole chiave")))
        for row, (name, value) in enumerate(rows):
            ctk.CTkLabel(box, text=name, font=font(13, True), anchor="w", width=190).grid(
                row=row, column=0, sticky="nw", padx=(18, 8), pady=8)
            label = ctk.CTkLabel(box, text=value, font=font(13), anchor="w", justify="left", wraplength=480,
                                 text_color=PAL["text"])
            label.grid(row=row, column=1, sticky="w", padx=(0, 18), pady=8)
            self._wrapped.append(label)

    # ---- risultato e controlli
    def _validate(self) -> str:
        step = self.STEPS[self.index]
        if step == "protection" and self.current_protection == "plain" and self.mode.get() == "vault":
            if not self.phrase_var.get():
                return tr("Inserisci una passphrase.")
            if self.phrase_var.get() != self.repeat_var.get():
                return tr("Le due passphrase devono coincidere.")
            try:
                if not 1 <= int(self.minutes.get()) <= 1440:
                    raise ValueError
            except ValueError:
                return tr("Inserisci un tempo da 1 a 1440 minuti.")
        if step == "documents" and not [p for p, v in self.choices if v.get()]:
            return tr("Spunta almeno una cartella, oppure scegline una.")
        if step == "ocr" and self.ocr.get() and not any(v.get() for v in self.ocr_vars.values()):
            return tr("Per leggere scansioni e immagini spunta almeno una lingua.")
        return ""

    def result(self) -> dict:
        codes = [c for c, v in self.ocr_vars.items() if v.get()]
        mode = self.mode.get() if self.current_protection == "plain" else self.current_protection
        passphrase = self.phrase_var.get() if mode == "vault" and self.current_protection == "plain" else None
        try:
            minutes = int(self.minutes.get())
        except ValueError:
            minutes = 10
        return {"folders": [p for p, v in self.choices if v.get()], "recursive": bool(self.subfolders.get()),
                "auto_scan": bool(self.auto_scan.get()), "ocr": bool(self.ocr.get()),
                "ocr_lang": OcrLanguages.normalize(codes), "semantic": self.meaning.get() == "on" and self.meaning_ready,
                "protection": {"mode": mode, "passphrase": passphrase, "minutes": minutes,
                               "change": self.current_protection == "plain" and mode != "plain"}}

    def show_error(self, text: str):
        self.error.configure(text=text)

    def destroy(self):
        self.phrase_var.set("")  # la passphrase non deve restare in memoria più del necessario
        self.repeat_var.set("")
        super().destroy()
