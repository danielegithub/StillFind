# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Impostazioni."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

import customtkinter as ctk

from stillfind.core import config, system, labels as ui_text
from stillfind.core import facts
from stillfind.extractors.ocr import Tesseract
from stillfind.indexing.relations import RelationGraph
from stillfind.runtime.signals import AppSignals
from stillfind.semantic.models import ModelCatalog
from stillfind.semantic.search import SemanticIndex
from stillfind.ui import security_ui
from stillfind.ui.dialogs.about import AboutDialog
from stillfind.ui.dialogs.diagnostics import DiagnosticsDialog
from stillfind.ui.dialogs.duplicates import DuplicatesDialog
from stillfind.ui.theme import PAL, THEMES, font, ghost_button, primary_button, card

if TYPE_CHECKING:
    from stillfind.ui.app import App


class SettingsDialog(ctk.CTkToplevel):
    """Impostazioni su due livelli: in vista solo ciò che serve a tutti; il resto sotto «Opzioni avanzate»."""

    TEXT_TYPES = tuple((n, g) for n, g in ui_text.CATEGORIES if n != "Immagini e foto")

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title("Impostazioni")
        self.geometry("660x700")
        self.minsize(560, 520)
        self.configure(fg_color=PAL["bg"])
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        cfg = app.cfg
        self._initial_theme = cfg.get("theme", "system")
        self.folders: list[dict] = [dict(f) for f in cfg["folders"]]
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=14, pady=(14, 0))
        body.grid_columnconfigure(0, weight=1)

        # ---------- livello 1: quello che serve a tutti
        ctk.CTkLabel(body, text="Dove cercare", font=font(16, True), text_color=PAL["text"]
                     ).grid(row=0, column=0, sticky="w", pady=(4, 0))
        ctk.CTkLabel(body, text="StillFind legge i documenti di queste cartelle. Con «Anche sottocartelle» spuntato "
                                "legge pure tutto ciò che c'è al loro interno.",
                     font=font(12), text_color=PAL["muted"], wraplength=580, justify="left", anchor="w"
                     ).grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.list_card = card(body)
        self.list_card.grid(row=2, column=0, sticky="ew")
        self.list_card.grid_columnconfigure(0, weight=1)
        primary_button(body, "＋  Aggiungi cartella…", self.add).grid(row=3, column=0, sticky="w", pady=(8, 18))
        self._render_folders()

        ctk.CTkLabel(body, text="Scansioni e foto", font=font(16, True), text_color=PAL["text"]
                     ).grid(row=4, column=0, sticky="w")
        ok = Tesseract.available()
        self.ocr = ctk.BooleanVar(value=bool(cfg.get("ocr")))
        ctk.CTkSwitch(body, text="Leggi il testo anche dai PDF scansionati e dalle foto (più lento)",
                      variable=self.ocr, progress_color=PAL["accent"], text_color=PAL["text"], font=font(13)
                      ).grid(row=5, column=0, sticky="w", pady=(6, 0))
        ctk.CTkLabel(body, font=font(12), text_color=PAL["muted"], justify="left", anchor="w", wraplength=580,
                     text=("Pronto all'uso." if ok else
                           "Per usare questa funzione serve installare il programma gratuito Tesseract (vedi README).")
                     ).grid(row=6, column=0, sticky="w", pady=(2, 18))

        ctk.CTkLabel(body, text="Aspetto", font=font(16, True), text_color=PAL["text"]
                     ).grid(row=7, column=0, sticky="w")
        current = next((k for k, v in THEMES.items() if v == self._initial_theme), "Sistema")
        self.theme = ctk.CTkSegmentedButton(body, values=list(THEMES), selected_color=PAL["accent"],
                                            selected_hover_color=PAL["accent_hover"], font=font(13),
                                            command=self._preview_theme)
        self.theme.set(current)
        self.theme.grid(row=8, column=0, sticky="w", pady=(6, 18))

        # ---------- livello 2: opzioni avanzate (chiuse all'inizio)
        self.adv_btn = ghost_button(body, "▸  Opzioni avanzate", self._toggle_adv)
        self.adv_btn.grid(row=9, column=0, sticky="w")
        self.adv = ctk.CTkFrame(body, fg_color="transparent")
        self.adv.grid(row=10, column=0, sticky="ew", pady=(10, 0))
        self.adv.grid_columnconfigure(0, weight=1)
        self.adv.grid_columnconfigure(1, weight=1)
        self._adv_open = False

        ctk.CTkLabel(self.adv, text="Tipi di documento da cercare", font=font(15, True), text_color=PAL["text"]
                     ).grid(row=0, column=0, columnspan=2, sticky="w")
        have = {e.lower().lstrip(".") for e in cfg["extensions"]}
        self.cat_vars: dict[str, ctk.BooleanVar] = {}
        for i, (name, group) in enumerate(self.TEXT_TYPES):
            var = ctk.BooleanVar(value=bool(have & set(group)))
            self.cat_vars[name] = var
            ctk.CTkCheckBox(self.adv, text=f"{name}  ({', '.join(group[:3])}{'…' if len(group) > 3 else ''})",
                            variable=var, checkbox_width=20, checkbox_height=20, corner_radius=6,
                            fg_color=PAL["accent"], hover_color=PAL["accent_hover"], font=font(13),
                            text_color=PAL["text"]).grid(row=1 + i // 2, column=i % 2, sticky="w", pady=4, padx=(0, 8))
        r = 2 + (len(self.TEXT_TYPES) + 1) // 2
        self.excl = self._field(self.adv, r, "Cartelle da escludere",
                                "Nomi separati da virgola (per esempio: Temp, Cache). Vengono saltate ovunque si trovino.",
                                ", ".join(cfg["exclude_dirs"]))
        limits = ctk.CTkFrame(self.adv, fg_color="transparent")
        limits.grid(row=self.adv.grid_size()[1], column=0, columnspan=2, sticky="ew")
        limits.grid_columnconfigure((0, 1), weight=1, uniform="campi")
        self.ocr_lang = self._mini_field(limits, 0, 0, "Lingua OCR (es. ita+eng)",
                                         str(cfg.get("ocr_lang", "ita+eng")))
        self.ocr_max_pages = self._mini_field(limits, 0, 1, "Pagine massime per PDF scansionato",
                                              str(cfg.get("ocr_max_pages", 50)))
        self.max_file_mb = self._mini_field(limits, 2, 0, "Dimensione massima dei file (MB)",
                                            str(cfg.get("max_file_mb", 50)))
        self.auto_scan = self._mini_field(limits, 2, 1, "Controllo novità (secondi, 0 = mai)",
                                          str(cfg.get("auto_scan_seconds", 60)))
        self.pause_battery = ctk.BooleanVar(value=bool(cfg.get("pause_on_battery")))
        ctk.CTkSwitch(self.adv, text="Non leggere i documenti quando il computer va a batteria",
                      variable=self.pause_battery, progress_color=PAL["accent"], text_color=PAL["text"],
                      font=font(13)).grid(row=r + 6, column=0, columnspan=2, sticky="w", pady=(12, 4))
        ready = ModelCatalog.is_installed() and SemanticIndex.libraries_available()
        self.semantic = ctk.BooleanVar(value=bool(cfg.get("semantic_search")) and ready)
        sw = ctk.CTkSwitch(self.adv, text="Cerca anche per significato (non solo per parole esatte)",
                           variable=self.semantic, progress_color=PAL["accent"], text_color=PAL["text"], font=font(13),
                           state="normal" if ready else "disabled")
        sw.grid(row=r + 7, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ctk.CTkLabel(self.adv, text=("Calcola in background un «significato» per ogni passaggio; tutto resta sul computer."
                                     if ready else "Non disponibile: manca il componente (modello e librerie) in questa installazione."),
                     font=font(11), text_color=PAL["muted"], anchor="w", wraplength=580, justify="left"
                     ).grid(row=r + 8, column=0, columnspan=2, sticky="w", pady=(0, 4))
        tools = ctk.CTkFrame(self.adv, fg_color="transparent")
        tools.grid(row=r + 9, column=0, columnspan=2, sticky="w", pady=(8, 6))
        ghost_button(tools, "Stato dell'archivio…", self._open_diag).pack(side="left")
        ghost_button(tools, "Documenti duplicati…", self._open_dups).pack(side="left", padx=8)
        ghost_button(tools, "Informazioni…", lambda: AboutDialog(self)).pack(side="left")
        ctk.CTkLabel(self.adv, text=f"Archivio: {config.DB_PATH}", font=font(11), text_color=PAL["muted"],
                     anchor="w", wraplength=580, justify="left"
                     ).grid(row=r + 10, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ctk.CTkLabel(self.adv, text="Entità da collegare", font=font(13, True)).grid(
            row=r+11, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.entity_vars = {}
        for i, (kind, label) in enumerate(facts.ENTITY_LABELS.items()):
            var = ctk.BooleanVar(value=kind in cfg.get("entity_types", facts.ENTITY_LABELS))
            self.entity_vars[kind] = var
            ctk.CTkCheckBox(self.adv, text=label, variable=var, font=font(12)).grid(
                row=r+12+i//2, column=i%2, sticky="w", pady=3)
        self.prefixes = self._field(self.adv, r+16, "Sigle dei protocolli e delle pratiche",
                                    "Separa le sigle con una virgola, ad esempio PR, PROT.",
                                    ", ".join(cfg.get("protocol_prefixes", facts.PROTOCOL_PREFIXES)))
        self.alerts = ctk.BooleanVar(value=cfg.get("deadline_alerts", True))
        ctk.CTkSwitch(self.adv, text="Avvisa delle scadenze nei prossimi 30 giorni", variable=self.alerts,
                      font=font(13)).grid(row=r+19, column=0, columnspan=2, sticky="w", pady=6)
        self.shortcut = ctk.BooleanVar(value=cfg.get("global_shortcut", True))
        ctk.CTkSwitch(self.adv, text=f"Apri la ricerca da qualsiasi finestra con {AppSignals.LABEL}",
                      variable=self.shortcut, font=font(13)).grid(row=r+20, column=0, columnspan=2, sticky="w", pady=6)
        ghost_button(self.adv, "Protezione e cifratura dell’archivio…",
                     lambda: security_ui.restart(app, True)).grid(row=r+21, column=0, columnspan=2, sticky="w", pady=8)
        self.retention = self._field(self.adv, r+22, "Conservazione dei dischi scollegati",
                                     "Giorni dalla prima assenza rilevata. Con 0 i file restano nell’indice.",
                                     str(cfg.get("offline_retention_days", 0)))
        self.restore_paths = []
        forgotten = [p for p, in app.con.execute("SELECT path FROM esclusioni ORDER BY path")]
        if forgotten:
            ctk.CTkLabel(self.adv, text="Percorsi dimenticati", font=font(13, True)).grid(
                row=r+25, column=0, columnspan=2, sticky="w", pady=(12, 2))
            self.forgotten = ttk.Combobox(self.adv, state="readonly", values=forgotten, width=35)
            self.forgotten.grid(row=r+26, column=0, sticky="ew")
            self.forgotten.current(0)
            ghost_button(self.adv, "Includi di nuovo", self._include_again).grid(row=r+26, column=1, sticky="w")
        self.adv.grid_remove()

        # ---------- pulsanti
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=14, pady=14)
        bar.grid_columnconfigure(0, weight=1)
        ghost_button(bar, "Annulla", self._cancel).grid(row=0, column=1, padx=8)
        primary_button(bar, "Salva", self.save).grid(row=0, column=2)
        self.after(150, self._grab)

    def _include_again(self):
        path = self.forgotten.get()
        if path:
            self.restore_paths.append(path)
            remaining = [p for p in self.forgotten["values"] if p != path]
            self.forgotten.configure(values=remaining)
            self.forgotten.set(remaining[0] if remaining else "")

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        if self._adv_open:
            self.adv.grid()
            self.adv_btn.configure(text="▾  Opzioni avanzate")
        else:
            self.adv.grid_remove()
            self.adv_btn.configure(text="▸  Opzioni avanzate")

    def _grab(self):
        try:
            self.lift()
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass

    def _field(self, parent, row, title, hint, value):
        ctk.CTkLabel(parent, text=title, font=font(15, True), text_color=PAL["text"]
                     ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ctk.CTkLabel(parent, text=hint, font=font(12), text_color=PAL["muted"], anchor="w", wraplength=560,
                     justify="left").grid(row=row + 1, column=0, columnspan=2, sticky="w")
        e = ctk.CTkEntry(parent, height=36, corner_radius=10, fg_color=PAL["card"], border_color=PAL["line"],
                         text_color=PAL["text"])
        e.insert(0, value)
        e.grid(row=row + 2, column=0, columnspan=2, sticky="ew", pady=(4, 4))
        return e

    def _mini_field(self, parent, row, col_i, title, value):
        pad = (0, 8) if col_i == 0 else (8, 0)
        ctk.CTkLabel(parent, text=title, font=font(12), text_color=PAL["muted"], anchor="w", wraplength=260,
                     justify="left").grid(row=row, column=col_i, sticky="w", padx=pad, pady=(10, 0))
        e = ctk.CTkEntry(parent, height=34, corner_radius=10, fg_color=PAL["card"], border_color=PAL["line"],
                         text_color=PAL["text"])
        e.insert(0, value)
        e.grid(row=row + 1, column=col_i, sticky="ew", padx=pad, pady=(2, 2))
        return e

    def _render_folders(self):
        for w in self.list_card.winfo_children():
            w.destroy()
        if not self.folders:
            ctk.CTkLabel(self.list_card, text="Nessuna cartella: aggiungine almeno una.",
                         text_color=PAL["muted"], font=font(13)).grid(row=0, column=0, padx=14, pady=16)
            return
        for i, f in enumerate(self.folders):
            ctk.CTkLabel(self.list_card, text=f["path"], anchor="w", font=font(13), text_color=PAL["text"]
                         ).grid(row=i, column=0, sticky="ew", padx=(14, 6), pady=6)
            var = ctk.BooleanVar(value=f["recursive"])
            ctk.CTkCheckBox(self.list_card, text="Anche sottocartelle", variable=var, width=150, checkbox_width=20,
                            checkbox_height=20, corner_radius=6, fg_color=PAL["accent"],
                            hover_color=PAL["accent_hover"], font=font(12), text_color=PAL["text"],
                            command=lambda f=f, v=var: f.__setitem__("recursive", bool(v.get()))
                            ).grid(row=i, column=1, padx=6)
            ctk.CTkButton(self.list_card, text="✕", width=30, height=28, corner_radius=8,
                          fg_color="transparent", text_color=PAL["muted"], hover_color=PAL["ghost_hover"],
                          command=lambda p=f: self.remove(p)).grid(row=i, column=2, padx=(0, 8))

    def add(self):
        d = filedialog.askdirectory(parent=self, title="Scegli una cartella")
        if d and d not in [f["path"] for f in self.folders]:
            self.folders.append({"path": d, "recursive": True})
            self._render_folders()
        self._grab()

    def remove(self, path):
        self.folders.remove(path)
        self._render_folders()

    def _preview_theme(self, label):
        ctk.set_appearance_mode(THEMES[label])
        self.app.after(60, self.app.restyle)

    def _open_diag(self):
        self._cancel()
        self.app.after(60, lambda: DiagnosticsDialog(self.app))

    def _open_dups(self):
        self._cancel()
        self.app.after(60, lambda: DuplicatesDialog(self.app))

    def _cancel(self):
        if THEMES[self.theme.get()] != self._initial_theme:
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
            messagebox.showerror("Impostazioni", f"Valore non valido per «{label}»: inserisci un numero intero.",
                                 parent=self)
            return None
        if v < minimum:
            messagebox.showerror("Impostazioni", f"«{label}» deve essere almeno {minimum}.", parent=self)
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
        return out + [e for e in cur if e not in known and e not in ui_text.category_exts("Immagini e foto")]

    def save(self):
        ocr_max = self._parse_int(self.ocr_max_pages, "Pagine massime lette per ogni PDF scansionato", 1, 50)
        if ocr_max is None:
            return
        max_mb = self._parse_int(self.max_file_mb, "Dimensione massima dei file (MB)", 1, 50)
        if max_mb is None:
            return
        auto_sec = self._parse_int(self.auto_scan, "Controllo novità (secondi)", 0, 60)
        if auto_sec is None:
            return
        days = self._parse_int(self.retention, "Giorni di conservazione", 0, 0)
        if days is None:
            return
        exts = self._extensions()
        if not exts:
            messagebox.showerror("Impostazioni", "Spunta almeno un tipo di documento da cercare "
                                 "(sotto «Opzioni avanzate»).", parent=self)
            return

        def split(text):
            return [x.strip() for x in text.split(",") if x.strip()]

        c = self.app.cfg
        c["folders"] = self.folders
        c["extensions"] = exts
        c["exclude_dirs"] = split(self.excl.get())
        c["ocr"] = bool(self.ocr.get())
        c["ocr_lang"] = self.ocr_lang.get().strip() or "ita+eng"
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
        c["theme"] = THEMES[self.theme.get()]
        c.save()
        with self.app.con:
            self.app.con.executemany("DELETE FROM esclusioni WHERE path=?", ((p,) for p in self.restore_paths))
            RelationGraph(self.app.con).set_rules(c)
        if not c["semantic_search"]:
            self.app._embedder = None
        self.app.links.reset()
        self.app._refresh_filters()
        self.app.do_search(force=True)
        self.destroy()
        self.app.reschedule_auto_scan()
        self.app.start_index(force=True)


# --------------------------------------------------------------------------
