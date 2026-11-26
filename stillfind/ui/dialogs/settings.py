# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Impostazioni organizzate in cinque schede tematiche."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING

import customtkinter as ctk

from stillfind.core import config, labels as ui_text, facts, i18n

from stillfind.core.ocr_languages import OcrLanguages
from stillfind.core.i18n import mark, tr
from stillfind.extractors.ocr import Tesseract
from stillfind.indexing.relations import RelationGraph
from stillfind.runtime.autostart import Autostart
from stillfind.runtime.signals import AppSignals
from stillfind.semantic.models import ModelCatalog, DEFAULT_MODEL
from stillfind.semantic.search import SemanticIndex
from stillfind.storage.protection import ArchiveProtection
from stillfind.ui import security_ui
from stillfind.ui.dialogs.ocr_change import OcrLanguageChangeDialog
from stillfind.ui.theme import PAL, THEME_IDS, font, ghost_button, primary_button, card
from stillfind.ui.widgets import IdSelector, SquareToggle, SquareEntry, SquareComboBox, SquareCheckBox

if TYPE_CHECKING:
    from stillfind.ui.app import App


class SettingsDialog(ctk.CTkToplevel):
    """Schede indipendenti, con salvataggio unico e anteprima reversibile del tema."""

    # Le schede si riconoscono dall'identificatore; il testo mostrato è tradotto.
    TAB_IDS = ("files", "ocr", "search", "security", "system")
    TAB_NAMES = ui_text.Choices((("files", mark("Cartelle e File")), ("ocr", mark("Lettura e OCR")),
                                 ("search", mark("Ricerca e Significato")), ("security", mark("Sicurezza e Privacy")),
                                 ("system", mark("Aspetto e Sistema"))))
    THEME_NAMES = ui_text.Choices((("system", mark("Sistema")), ("light", mark("Chiaro")), ("dark", mark("Scuro"))))
    TEXT_TYPES = tuple((n, g) for n, g in ui_text.CATEGORIES if n != "images")

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title(tr("Impostazioni"))
        self.geometry("920x740")
        self.minsize(860, 540)
        self.configure(fg_color=PAL["bg"])
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._initial_theme = app.cfg.get("theme", "system")
        self._initial_language = i18n.Languages.effective(app.cfg.get("ui_language"))
        self.folders = [dict(f) for f in app.cfg["folders"]]
        self.restore_paths = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.tabs = IdSelector(self, list(zip(self.TAB_IDS, self.TAB_NAMES.labels())), command=self._show_tab,
                               font=font(12), height=38)
        self.tabs.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 0))
        self.pages = {}
        for name in self.TAB_IDS:
            page = ctk.CTkScrollableFrame(self, corner_radius=0, border_width=1,
                                         border_color=PAL["line"], fg_color=PAL["card"])
            page.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 0))
            page.grid_columnconfigure((0, 1), weight=1, uniform="fields")
            self.pages[name] = page
        for build, name in zip((self._build_files, self._build_ocr, self._build_search,
                                self._build_security, self._build_system), self.TAB_IDS):
            build(self.pages[name])
        self._show_tab(self.TAB_IDS[0])
        for name, button in ((key, self.tabs.button(key)) for key in self.TAB_IDS):
            tk.Frame.configure(button, takefocus=True)
            for key in ("<Return>", "<space>"):
                tk.Misc.bind(button, key, lambda e, n=name: self._show_tab(n))
            for key, step in (("<Right>", 1), ("<Left>", -1)):
                tk.Misc.bind(button, key, lambda e, d=step: self._next_tab(d))
            tk.Misc.bind(button, "<FocusIn>", lambda e, b=button: b.configure(border_color=PAL["focus"], border_width=1))
            tk.Misc.bind(button, "<FocusOut>", lambda e, b=button: b.configure(border_color=PAL["line"], border_width=1))
        self.bind("<Control-Tab>", lambda e: self._next_tab(1))
        self.bind("<Control-Shift-Tab>", lambda e: self._next_tab(-1))
        self.bind("<Escape>", lambda e: self._cancel())
        bar = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=16, pady=16)
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text=tr("Le modifiche si applicano con Salva."), font=font(12),
                     text_color=PAL["muted"]).grid(row=0, column=0, sticky="w")
        self.cancel_button = ghost_button(bar, tr("Annulla"), self._cancel, width=100)
        self.cancel_button.grid(row=0, column=1, padx=8)
        self.save_button = primary_button(bar, tr("Salva"), self.save, width=100)
        self.save_button.grid(row=0, column=2)
        self.after(150, self._grab)

    def _show_tab(self, name):
        self.tabs.set(name)
        for key, page in self.pages.items():
            # CTkScrollableFrame delegates grid to its outer frame.
            if key == name:
                page.grid()
            else:
                page.grid_remove()
        return "break"

    def _next_tab(self, step):
        name = self.TAB_IDS[(self.TAB_IDS.index(self.tabs.get()) + step) % len(self.TAB_IDS)]
        self._show_tab(name)
        tk.Misc.focus_set(self.tabs.button(name))
        return "break"

    def _focus_field(self, entry):
        for name, page in self.pages.items():
            parent = entry.master
            while parent is not self:
                if parent is page:
                    self._show_tab(name)
                    page._parent_canvas.yview_moveto(0)
                    self.update_idletasks()
                    canvas = page._parent_canvas
                    y = entry.winfo_rooty() - page.winfo_rooty()
                    canvas.yview_moveto(max(0, y - 80) / max(1, page.winfo_height()))
                    entry.focus_set()
                    return
                parent = parent.master

    def _heading(self, parent, row, title, hint=""):
        ctk.CTkLabel(parent, text=title, font=font(16, True), text_color=PAL["text"]).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(16, 2))
        if hint:
            self._note(parent, row + 1, hint)

    def _note(self, parent, row, text):
        label = ctk.CTkLabel(parent, text=text, font=font(12), text_color=PAL["muted"],
                           wraplength=750, justify="left", anchor="w")
        label.grid(row=row, column=0, columnspan=2, sticky="ew", padx=14, pady=(2, 8))
        return label

    def _toggle(self, parent, row, text, key, default=False, **kwargs):
        var = ctk.BooleanVar(value=bool(self.app.cfg.get(key, default)))
        toggle = SquareToggle(parent, text=text, variable=var, font=font(13), **kwargs)
        toggle.grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=8)
        return var

    def _build_files(self, page):
        cfg = self.app.cfg
        self._heading(page, 0, tr("Cartelle indicizzate"), tr("Scegli dove cercare e se includere anche le sottocartelle."))
        self.list_card = card(page)
        self.list_card.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14)
        self.list_card.grid_columnconfigure(0, weight=1)
        self._render_folders()
        primary_button(page, tr("Aggiungi cartella…"), self.add, width=180).grid(row=3, column=0, sticky="w", padx=14, pady=8)
        self.excl = self._field(page, 4, tr("Cartelle da escludere"),
                               tr("Nomi separati da virgola (es. Temp, Cache, *.app), esclusi ovunque si trovino. "
                                  "L’elenco iniziale esclude dipendenze, compilazioni e cartelle di sistema."),
                               ", ".join(cfg["exclude_dirs"]))
        self.excl_files = self._field(page, 20, tr("File da escludere"),
                                      tr("Nomi o modelli separati da virgola (es. *.lock, package.json): codice sorgente, "
                                         "file generati e di servizio."),
                                      ", ".join(cfg.get("exclude_files", [])))
        self._heading(page, 7, tr("Tipi di documento da cercare"))
        have = {e.lower().lstrip(".") for e in cfg["extensions"]}
        self.cat_vars = {}
        for i, (name, group) in enumerate(self.TEXT_TYPES):
            var = ctk.BooleanVar(value=bool(have & set(group)))
            self.cat_vars[name] = var
            SquareCheckBox(page, text=f"{ui_text.CATEGORY_NAMES.label(name)} ({', '.join(group[:3])}{'…' if len(group) > 3 else ''})",
                           variable=var, corner_radius=0, border_width=1, font=font(12),
                           checkbox_width=20, checkbox_height=20).grid(
                row=8+i//2, column=i%2, sticky="w", padx=14, pady=5)
        self._note(page, 12, tr("Immagini e foto vengono incluse quando attivi l’OCR nella scheda Lettura e OCR."))
        self.max_file_mb = self._mini_field(page, 13, 0, tr("Dimensione massima file (MB)"), str(cfg.get("max_file_mb", 50)))
        self.retention = self._mini_field(page, 13, 1, tr("Conservazione dischi scollegati (giorni)"), str(cfg.get("offline_retention_days", 0)))
        self._note(page, 15, tr("Con 0 giorni i documenti dei dischi scollegati restano nell’indice senza scadenza."))
        self._heading(page, 16, tr("Percorsi dimenticati"), tr("Il ripristino viene applicato al salvataggio delle impostazioni."))
        forgotten = [p for p, in self.app.con.execute("SELECT path FROM esclusioni ORDER BY path")]
        self.forgotten = SquareComboBox(page, values=forgotten or [""], state="readonly", font=font(12))
        self.forgotten.set(forgotten[0] if forgotten else "")
        self.forgotten.grid(row=18, column=0, sticky="ew", padx=14, pady=(0, 14))
        self.restore_button = ghost_button(page, tr("Includi di nuovo"), self._include_again, width=160)
        self.restore_button.grid(row=18, column=1, sticky="w", padx=14, pady=(0, 14))
        if not forgotten:
            self.restore_button.configure(state="disabled")

    def _build_ocr(self, page):
        cfg = self.app.cfg
        self._heading(page, 0, tr("Lettura di scansioni e immagini"))
        self.ocr = self._toggle(page, 1, tr("Leggi il testo dai PDF scansionati e dalle immagini"), "ocr")
        self._note(page, 2, tr("Tesseract pronto all’uso. OCR e lingue sono già inclusi nell’app distribuita.")
                   if Tesseract.available() else tr("Componente OCR non disponibile in questa installazione. Nell’app distribuita Tesseract e le lingue sono già inclusi."))
        self._heading(page, 3, tr("Lingue dei documenti"),
                      tr("Spunta le lingue in cui sono scritte le scansioni. Sono tutte incluse nell’app e funzionano senza internet: "
                         "sceglierne poche rende la lettura più rapida e precisa."))
        chosen = OcrLanguages.parse(cfg.get("ocr_lang")) or OcrLanguages.parse(OcrLanguages.DEFAULT)
        self.ocr_lang_vars = {}
        for i, (code, name) in enumerate(OcrLanguages.LANGUAGES):
            var = ctk.BooleanVar(value=code in chosen)
            self.ocr_lang_vars[code] = var
            SquareCheckBox(page, text=OcrLanguages.name(code), variable=var, corner_radius=0, border_width=1,
                           checkbox_width=20, checkbox_height=20, font=font(12)).grid(
                row=5+i//2, column=i%2, sticky="w", padx=14, pady=5)
        self.ocr_max_pages = self._mini_field(page, 8, 0, tr("Pagine massime OCR per PDF"), str(cfg.get("ocr_max_pages", 50)))
        self._heading(page, 10, tr("Scansione automatica"), tr("Controlla periodicamente se nelle cartelle ci sono documenti nuovi o modificati."))
        self.auto_scan = self._mini_field(page, 12, 0, tr("Intervallo in secondi (0 = disattivata)"), str(cfg.get("auto_scan_seconds", 60)))
        self.pause_battery = self._toggle(page, 14, tr("Metti in pausa la lettura quando il computer va a batteria"), "pause_on_battery")

    def _build_search(self, page):
        cfg = self.app.cfg
        ready = ModelCatalog.is_installed() and SemanticIndex.libraries_available()
        self._heading(page, 0, tr("Ricerca per significato"), tr("Trova passaggi affini anche quando usano parole diverse. Tutto viene calcolato sul computer."))
        # Conserva la preferenza anche se un componente è temporaneamente assente.
        self.semantic = self._toggle(page, 2, tr("Cerca anche per significato"), "semantic_search",
                                     state="normal" if ready else "disabled")
        status = SemanticIndex(self.app.con).status()
        self._note(page, 3, tr("Modello locale: {model} · {state}", model=DEFAULT_MODEL,
                               state=tr("disponibile") if ready else tr("componente non disponibile in questa installazione")) + "\n" +
                   tr("{passages} passaggi indicizzati · {done} documenti elaborati su {docs} · {pending} da elaborare.",
                      passages=status["passages"], done=status["done"], docs=status["docs"], pending=status["pending"]))
        self._heading(page, 4, tr("Entità da riconoscere e collegare"))
        self.entity_vars = {}
        for i, kind in enumerate(facts.ENTITY_LABELS):
            var = ctk.BooleanVar(value=kind in cfg.get("entity_types", facts.ENTITY_LABELS))
            self.entity_vars[kind] = var
            SquareCheckBox(page, text=facts.entity_label(kind), variable=var, corner_radius=0, border_width=1,
                           checkbox_width=20, checkbox_height=20, font=font(12)).grid(
                row=5+i//2, column=i%2, sticky="w", padx=14, pady=5)
        row = 5 + (len(self.entity_vars) + 1)//2
        self.prefixes = self._field(page, row, tr("Sigle dei protocolli e delle pratiche"),
                                   tr("Separa le sigle con una virgola, per esempio PR, PROT."),
                                   ", ".join(cfg.get("protocol_prefixes", facts.PROTOCOL_PREFIXES)))
        self.alerts = self._toggle(page, row+3, tr("Avvisa delle scadenze nei prossimi 30 giorni"), "deadline_alerts", True)

    def _build_security(self, page):
        mode = ArchiveProtection.settings(config.DB_PATH)["mode"]
        self._heading(page, 0, tr("Protezione dell’archivio"), tr("Stato attuale: {mode}", mode=tr(security_ui.MODES[mode])))
        self._note(page, 2, tr("Archivio: {path}", path=config.DB_PATH))
        self._note(page, 3, tr("In chiaro: archivio leggibile sul disco.\nPortachiavi: chiave custodita dall’account del computer.\nCassaforte: passphrase richiesta all’apertura e blocco per inattività."))
        ghost_button(page, tr("Salva e configura cifratura…"), lambda: self.save(configure_protection=True), width=270).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=14, pady=8)
        self._note(page, 5, tr("La configurazione della cifratura salva le modifiche e riavvia StillFind."))
        self.secret_controls = security_ui.SecretControls(page, self.app)
        self.secret_controls.grid(row=6, column=0, columnspan=2, sticky="ew", padx=14, pady=14)

    def _build_system(self, page):
        self._heading(page, 0, tr("Aspetto"), tr("Il tema viene mostrato in anteprima. Annulla ripristina quello precedente."))
        current = self._initial_theme if self._initial_theme in THEME_IDS else "system"
        self.theme = IdSelector(page, list(zip(self.THEME_NAMES.ids, self.THEME_NAMES.labels())), font=font(13),
                                command=self._preview_theme, width=300, height=34, dynamic_resizing=False)
        self.theme.set(current)
        self.theme.grid(row=2, column=0, columnspan=2, sticky="w", padx=14, pady=8)
        self._heading(page, 3, tr("Scorciatoia globale"))
        self.shortcut = self._toggle(page, 4, tr("Apri la ricerca da qualsiasi finestra con {keys}", keys=AppSignals.LABEL), "global_shortcut", True)
        self._heading(page, 5, tr("Lingua"), tr("Lingua di menu, pulsanti e messaggi. Non cambia le lingue di lettura dei documenti (OCR). "
                                                 "La nuova lingua si applica alla prossima apertura di StillFind: chiudi e riapri l’app."))
        self.language_names = {name: code for code, name in i18n.Languages.ALL}
        self.language = SquareComboBox(page, values=list(self.language_names), state="readonly", font=font(13))
        self.language.set(i18n.Languages.NATIVE_NAMES[self._initial_language])
        self.language.grid(row=7, column=0, sticky="ew", padx=14, pady=(0, 14))
        self.language_note = ctk.CTkLabel(page, text="", font=font(12), text_color=PAL["muted"], anchor="w",
                                          justify="left", wraplength=360)
        self.language_note.grid(row=7, column=1, sticky="w", padx=14, pady=(0, 14))
        self.language.configure(command=self._language_changed)
        self._heading(page, 8, tr("Avvio e icona di sistema"),
                      tr("L’icona permette di aprire la ricerca e mettere in pausa la lettura senza aprire la finestra."))
        self.tray = self._toggle(page, 10, tr("Mostra l’icona nella barra di sistema"), "show_tray", True)
        try:
            launch = Autostart.is_enabled()
        except Exception:
            launch = bool(self.app.cfg.get("launch_at_login", False))
        self._launch_initial = launch
        self.launch = ctk.BooleanVar(value=launch)
        SquareToggle(page, text=tr("Avvia StillFind all’accesso al computer"), variable=self.launch, font=font(13)).grid(
            row=11, column=0, columnspan=2, sticky="w", padx=14, pady=8)
        self._note(page, 12, tr("All’avvio parte in silenzio, senza aprire la finestra: la apri dall’icona o dalla scorciatoia."))

    def _language_changed(self, name):
        changed = self.language_names.get(name) != self._initial_language
        self.language_note.configure(text=tr("Si applicherà alla prossima apertura dell’app: chiudila e riaprila.") if changed else "")

    def _include_again(self):
        path = self.forgotten.get()
        if path:
            self.restore_paths.append(path)
            remaining = [p for p in self.forgotten.cget("values") if p != path]
            self.forgotten.configure(values=remaining or [""])
            self.forgotten.set(remaining[0] if remaining else "")
            if not remaining:
                self.restore_button.configure(state="disabled")

    def _grab(self):
        try:
            self.lift()
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass

    def _field(self, parent, row, title, hint, value):
        ctk.CTkLabel(parent, text=title, font=font(15, True), text_color=PAL["text"]
                     ).grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(14, 0))
        ctk.CTkLabel(parent, text=hint, font=font(12), text_color=PAL["muted"], anchor="w", wraplength=560,
                     justify="left").grid(row=row + 1, column=0, columnspan=2, sticky="w", padx=14)
        e = SquareEntry(parent, height=36, border_width=1, fg_color=PAL["card"], border_color=PAL["line"],
                         text_color=PAL["text"])
        e.insert(0, value)
        e.grid(row=row + 2, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 4))
        return e

    def _mini_field(self, parent, row, col_i, title, value):
        pad = (14, 8) if col_i == 0 else (8, 14)
        ctk.CTkLabel(parent, text=title, font=font(12), text_color=PAL["muted"], anchor="w", wraplength=260,
                     justify="left").grid(row=row, column=col_i, sticky="w", padx=pad, pady=(10, 0))
        e = SquareEntry(parent, height=34, border_width=1, fg_color=PAL["card"], border_color=PAL["line"],
                         text_color=PAL["text"])
        e.insert(0, value)
        e.grid(row=row + 1, column=col_i, sticky="ew", padx=pad, pady=(2, 2))
        return e

    def _render_folders(self):
        for w in self.list_card.winfo_children():
            w.destroy()
        if not self.folders:
            ctk.CTkLabel(self.list_card, text=tr("Nessuna cartella: aggiungine almeno una."),
                         text_color=PAL["muted"], font=font(13)).grid(row=0, column=0, padx=14, pady=16)
            return
        for i, f in enumerate(self.folders):
            ctk.CTkLabel(self.list_card, text=f["path"], anchor="w", wraplength=430, justify="left", font=font(13), text_color=PAL["text"]
                         ).grid(row=i, column=0, sticky="ew", padx=(14, 6), pady=6)
            var = ctk.BooleanVar(value=f["recursive"])
            SquareCheckBox(self.list_card, text=tr("Anche sottocartelle"), variable=var, width=150, checkbox_width=20,
                            checkbox_height=20, corner_radius=0, border_width=1, fg_color=PAL["accent"],
                            hover_color=PAL["accent_hover"], font=font(12), text_color=PAL["text"],
                            command=lambda f=f, v=var: f.__setitem__("recursive", bool(v.get()))
                            ).grid(row=i, column=1, padx=6)
            ctk.CTkButton(self.list_card, text="✕", width=30, height=28, corner_radius=0, border_width=1,
                          fg_color="transparent", text_color=PAL["muted"], hover_color=PAL["ghost_hover"],
                          command=lambda p=f: self.remove(p)).grid(row=i, column=2, padx=(0, 8))

    def add(self):
        d = filedialog.askdirectory(parent=self, title=tr("Scegli una cartella"))
        if d and d not in [f["path"] for f in self.folders]:
            self.folders.append({"path": d, "recursive": True})
            self._render_folders()
        self._grab()

    def remove(self, path):
        self.folders.remove(path)
        self._render_folders()

    def _preview_theme(self, theme):
        ctk.set_appearance_mode(theme)
        self.app.after(60, self.app.restyle)

    def _cancel(self):
        if self.theme.get() != self._initial_theme:
            ctk.set_appearance_mode(self._initial_theme)
            self.app.after(60, self.app.restyle)
        self.destroy()

    def _parse_int(self, entry, label, minimum, default):
        raw = entry.get().strip()
        if not raw:
            return default
        try:
            v = int(raw)
        except ValueError:
            messagebox.showerror(tr("Impostazioni"), tr("Valore non valido per «{label}»: inserisci un numero intero.", label=label),
                                 parent=self)
            self._focus_field(entry)
            return None
        if v < minimum:
            messagebox.showerror(tr("Impostazioni"), tr("«{label}» deve essere almeno {minimum}.", label=label, minimum=minimum), parent=self)
            self._focus_field(entry)
            return None
        return v

    def _extensions(self) -> list[str]:
        """Estensioni dalle caselle dei tipi. Quelle già presenti e quelle personalizzate si conservano."""
        cur = [e.lower().lstrip(".") for e in self.app.cfg["extensions"]]
        out: list[str] = []
        known: set[str] = set()
        for name, group in self.TEXT_TYPES:
            known |= set(group)
            if self.cat_vars[name].get():
                out += [e for e in cur if e in group] or list(group)
        return out + [e for e in cur if e not in known and e not in ui_text.category_exts("images")]

    def save(self, *, configure_protection=False):
        ocr_langs = [code for code, var in self.ocr_lang_vars.items() if var.get()]
        if self.ocr.get() and not ocr_langs:
            messagebox.showerror(tr("Impostazioni"), tr("Per leggere scansioni e immagini spunta almeno una lingua "
                                 "nella scheda «Lettura e OCR»."), parent=self)
            self._show_tab("ocr")
            return
        ocr_max = self._parse_int(self.ocr_max_pages, tr("Pagine massime lette per ogni PDF scansionato"), 1, 50)
        if ocr_max is None:
            return
        max_mb = self._parse_int(self.max_file_mb, tr("Dimensione massima dei file (MB)"), 1, 50)
        if max_mb is None:
            return
        auto_sec = self._parse_int(self.auto_scan, tr("Controllo novità (secondi)"), 0, 60)
        if auto_sec is None:
            return
        days = self._parse_int(self.retention, tr("Giorni di conservazione"), 0, 0)
        if days is None:
            return
        exts = self._extensions()
        if not exts:
            messagebox.showerror(tr("Impostazioni"), tr("Spunta almeno un tipo di documento da cercare "
                                 "nella scheda «Cartelle e File»."), parent=self)
            return

        def split(text):
            return [x.strip() for x in text.split(",") if x.strip()]

        old_ocr_lang = OcrLanguages.normalize(self.app.cfg.get("ocr_lang"))
        new_ocr_lang = OcrLanguages.normalize(ocr_langs)
        reprocess = ""
        if (new_ocr_lang != old_ocr_lang and self.ocr.get() and Tesseract.available()
                and self.app.con.execute("SELECT 1 FROM docs WHERE ocr=1 LIMIT 1").fetchone()):
            choice = OcrLanguageChangeDialog(self).result
            if choice is None:
                return  # si torna alle Impostazioni senza salvare
            if choice == OcrLanguageChangeDialog.REPROCESS:
                reprocess = new_ocr_lang

        c = self.app.cfg
        c["folders"] = self.folders
        c["extensions"] = exts
        c["exclude_dirs"] = split(self.excl.get())
        c["exclude_files"] = split(self.excl_files.get())
        c["ocr"] = bool(self.ocr.get())
        c["ocr_lang"] = new_ocr_lang
        c["ocr_max_pages"] = ocr_max
        c["max_file_mb"] = max_mb
        c["offline_retention_days"] = days
        c["auto_scan_seconds"] = auto_sec
        c["pause_on_battery"] = bool(self.pause_battery.get())
        c["semantic_search"] = bool(self.semantic.get())
        c["entity_types"] = [kind for kind, var in self.entity_vars.items() if var.get()]
        c["protocol_prefixes"] = split(self.prefixes.get())
        c["deadline_alerts"] = self.alerts.get()
        c["global_shortcut"] = self.shortcut.get()
        c["theme"] = self.theme.get()
        c["show_tray"] = bool(self.tray.get())
        c["launch_at_login"] = bool(self.launch.get())
        c["ui_language"] = self.language_names.get(self.language.get(), self._initial_language)
        if bool(self.launch.get()) != self._launch_initial:
            try:
                Autostart.apply(bool(self.launch.get()))
            except Exception as exc:
                c["launch_at_login"] = self._launch_initial
                messagebox.showerror(tr("Impostazioni"), tr("Non riesco a cambiare l’avvio all’accesso: {error}",
                                                            error=exc), parent=self)
        c.save()
        with self.app.con:
            self.app.con.executemany("DELETE FROM esclusioni WHERE path=?", ((p,) for p in self.restore_paths))
            if new_ocr_lang != old_ocr_lang:
                # Una richiesta precedente non vale più; la nuova parte solo se l'utente l'ha scelta.
                self.app.con.execute("UPDATE stato SET rielabora_ocr=? WHERE id=1", (reprocess,))
            RelationGraph(self.app.con).set_rules(c)
        if not c["semantic_search"]:
            self.app._embedder = None
        self.app._update_models()
        self.app.links.reset()
        self.app._refresh_filters()
        self.app.do_search(force=True)
        self.destroy()
        self.app.reschedule_auto_scan()
        if configure_protection:
            security_ui.restart(self.app, True)
        else:
            self.app.start_index(force=True)


# --------------------------------------------------------------------------
