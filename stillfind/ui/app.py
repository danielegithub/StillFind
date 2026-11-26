# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Finestra principale di StillFind (customtkinter: stesso Tk, aspetto moderno).

Si avvia da `stillfind.cli` (`run()`).
Funziona in modalità chiara e scura (segue il sistema o si sceglie in Impostazioni)."""
from __future__ import annotations

import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from stillfind.core import about, config, errors, i18n, system, labels as ui_text

from stillfind.core.problems import ReadProblems
from stillfind.core.i18n import mark, tr, trn
from stillfind.extractors.ocr import Tesseract
from stillfind.indexing.relations import RelationGraph
from stillfind.runtime import agent
from stillfind.runtime.hotkey import Registration
from stillfind.runtime.signals import AppSignals
from stillfind.search.engine import HIT_START, SearchEngine
from stillfind.search.preview import DocumentPreview, OCCURRENCES_SHOWN
from stillfind.search.query import Query, WHERE_ALL, WHERE_NAME
from stillfind.semantic.models import ModelCatalog
from stillfind.semantic.search import SemanticIndex, SemanticSearch
from stillfind.storage.database import Database
from stillfind.storage.protection import ArchiveProtection
from stillfind.tools.export import CsvExporter
from stillfind.ui import security_ui
from stillfind.ui.brand import Brand
from stillfind.ui.dialogs.about import AboutDialog
from stillfind.ui.dialogs.audit import AuditDialog
from stillfind.ui.dialogs.compare import CompareDialog
from stillfind.ui.dialogs.date_range import DateRangeDialog
from stillfind.ui.dialogs.diagnostics import DiagnosticsDialog
from stillfind.ui.dialogs.document_list import DocumentList
from stillfind.ui.dialogs.duplicates import DuplicatesDialog
from stillfind.ui.dialogs.forget_folder import ForgetFolderDialog
from stillfind.ui.dialogs.help import HelpDialog
from stillfind.ui.dialogs.library import LibraryDialog
from stillfind.ui.dialogs.redact import RedactDialog
from stillfind.ui.dialogs.settings import SettingsDialog
from stillfind.ui.search_criteria import SearchCriteria
from stillfind.ui.theme import PAL, col, font, ghost_button, primary_button, card, configure_theme
from stillfind.ui.views.relations import RelationsPane
from stillfind.ui.views.results import ResultList
from stillfind.ui.views.intro import IntroView
from stillfind.ui.views.language import LanguageView
from stillfind.ui.views.setup import SetupView
from stillfind.ui.widgets import EmbeddingBadge, IdSelector, MenuBar, ModelsPanel, PreviewAction, SquareEntry, SquareOptionMenu


RECENT_COUNT = 15  # documenti mostrati quando la ricerca è vuota


class App(ctk.CTk):
    MODE_HITS, MODE_FULL, MODE_LINKS = "hits", "full", "links"

    def __init__(self, quit_token=None):
        self.cfg = config.Settings.load()
        ctk.set_appearance_mode(self.cfg.get("theme", "system"))
        configure_theme()
        super().__init__()
        self.title("StillFind")
        Brand.set_window_icon(self)
        self.geometry("1240x780")
        self.minsize(940, 600)
        self.configure(fg_color=PAL["bg"])
        self.con = Database.connect()
        self.protection = ArchiveProtection.settings(config.DB_PATH)
        ArchiveProtection.prepare_visibility(config.DB_PATH, self.cfg.get("hide_encryption_secret", False))
        self._last_input = time.monotonic()
        self._history_job = None
        self._history_signature = None
        self._closing = False
        self.msgs: queue.Queue = queue.Queue()
        self._preview_seq = 0  # numero dell'anteprima richiesta: scarta i calcoli superati
        self.indexing = False
        self._quit_token = AppSignals.quit_token() if quit_token is None else quit_token
        self._worker = None
        self._own_run = None
        self._request = None
        self._state = None
        self._pending_dialog = None
        self._pause_job = None
        self._reindex_pending: dict | None = None
        self._forget_queue = []
        self._auto_scan_job = None
        self.rows: dict[str, dict] = {}  # compatibilità: risultati dell'ultima ricerca (per l'esportazione)
        self._debounce = None
        self.folder_filter: str | None = None
        self._folder_map: dict[str, str] = {}
        self._silent = False
        self._last_scan = 0.0
        self._dirs_sig = None
        self._hint = None
        self._search_seq = 0           # numero della ricerca richiesta: i risultati superati si scartano
        self._search_con: sqlite3.Connection | None = None
        self._date_custom: tuple | None = None  # (dopo, prima, etichetta, testo «dal», testo «al») scelti a mano
        self._pv_content: str | None = None     # testo del documento in anteprima (None = non ancora letto)
        self._pv_layout = None
        self._pv_terms: list[str] = []
        self._pv_hits_parts: list | None = None  # anteprima a brani
        self._pv_full_parts: list | None = None  # testo completo evidenziato (si prepara alla prima richiesta)
        self._pv_full_busy = False
        self._pending_hit: int | None = None     # occorrenza da mostrare quando il testo completo è pronto
        self._anchor = None
        self._linked_row = None
        self._back_stack = []
        self._hit_ranges: list[tuple[str, str]] = []
        self._hit_i = -1
        self._embedder = None                   # modello del significato: si carica alla prima ricerca che lo usa
        self._embedder_lock = threading.Lock()
        self._embedder_failed = False
        self._forced_q: str | None = None  # testo cercato premendo Invio (anche se molto corto)
        self.welcome: SetupView | None = None
        self.language_view: LanguageView | None = None
        self.intro: IntroView | None = None
        self._build()
        self.restyle()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._shortcut = Registration(lambda: self.cfg.get("global_shortcut", True),
                                             AppSignals.signal_window, lambda s: self.msgs.put(("shortcut_error", s)))
        self._tray = Registration(lambda: self.cfg.get("show_tray", True), lambda action: self.msgs.put(("tray", action)),
                                         lambda _: None, tray=lambda: self._state)
        if self.protection["mode"] == "vault":
            for event in ("<KeyPress>", "<ButtonPress>", "<Motion>", "<MouseWheel>"):
                self.bind_all(event, self._activity, add="+")
        self.after(400, self._session_tick)
        self.after(150, self._poll)
        self.after(self.EMBEDDING_REFRESH_MS, self._refresh_embedding_states)
        self.bind("<FocusIn>", self._on_focus)
        self.reschedule_auto_scan()
        if not self.cfg["folders"]:
            if not self.cfg.get("ui_language"):
                self._show_language_choice()
            elif not self.cfg.get("intro_seen"):
                self._show_intro()
            else:
                self._show_welcome()
        else:
            self.start_index()

    # ---- primo avvio
    def _activity(self, event=None):
        self._last_input = time.monotonic()

    def _session_tick(self):
        if self._quit_token != AppSignals.quit_token():
            self._close()
            return
        if self.protection["mode"] == "vault" and time.monotonic() - self._last_input >= 60 * self.protection["minutes"]:
            security_ui.restart(self)
            return
        try:
            for action in AppSignals.WINDOW_ACTIONS:
                if AppSignals.consume(action):
                    self._window_action(action)
            if self._pending_dialog and not self.grab_current():
                action, self._pending_dialog = self._pending_dialog, None
                self._window_action(action)
            self._sync_worker()
        except Exception as exc:
            self.status.configure(text=tr("Stato dell’archivio non disponibile · {error}", error=exc))
        self.after(400, self._session_tick)

    def _window_action(self, action):
        if action == "quit":
            AppSignals.quit_all()
            self._close()
            return
        if action in ("pause", "resume"):
            if self._pause_job:
                self.after_cancel(self._pause_job)
                self._pause_job = None
            # Un documento grande può occupare il writer. Tk deve restare reattivo.
            if not self.con.try_set_paused(action == "pause"):
                self._pause_job = self.after(200, lambda: self._window_action(action))
                return
            if action == "resume":
                self.start_index(silent=True)
                AppSignals.signal("agent-wake")
            self._sync_worker()
            return
        self.deiconify()
        self.lift()
        self.focus_force()
        dialog = {"settings": SettingsDialog, "about": AboutDialog}.get(action)
        existing = next((w for w in self.winfo_children() if dialog and isinstance(w, dialog)), None)
        modal = self.grab_current()
        if existing or modal:
            window = existing or modal
            window.lift()
            window.focus_force()
            if modal and dialog and not existing:
                self._pending_dialog = action
        elif dialog:
            dialog(self)
        elif not self.welcome:
            self.entry.focus_set()

    def _show_language_choice(self):
        self.language_view = LanguageView(self, self._finish_language_choice)
        self.language_view.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.language_view.lift()

    def _finish_language_choice(self, code: str):
        """La lingua scelta vale subito: si salva, si ricostruisce la finestra con i nuovi testi e si prosegue."""
        self.cfg["ui_language"] = i18n.Translator.set_language(code)
        self.cfg.save()
        self.language_view.destroy()
        self.language_view = None
        for child in list(self.winfo_children()):
            child.destroy()
        self._build()
        self.restyle()
        self._show_intro()

    def _show_intro(self):
        self.intro = IntroView(self, self._finish_intro)
        self.intro.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.intro.lift()

    def _finish_intro(self):
        """Fine (o salto) della presentazione: si ricorda e si passa alla configurazione."""
        self.cfg["intro_seen"] = True
        self.cfg.save()
        if self.intro is not None:
            self.intro.destroy()
            self.intro = None
        self._show_welcome()

    def _show_welcome(self):
        self.welcome = SetupView(self, self._finish_setup, self.protection["mode"])
        self.welcome.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.welcome.lift()

    def _finish_welcome(self, paths: list[str]):
        """Compatibilità: solo le cartelle (le altre scelte restano quelle di prima)."""
        self._finish_setup({"folders": paths})

    def _finish_setup(self, choices: dict):
        """Fine della configurazione iniziale. La cifratura va applicata per prima, ad archivio ancora vuoto, così
        nasce già protetto; poi si salvano le scelte e parte la prima indicizzazione."""
        protection = choices.get("protection") or {}
        if protection.get("change"):
            try:
                self.con.close()
                ArchiveProtection.migrate(config.DB_PATH, protection["mode"], protection.get("passphrase"),
                                          protection.get("minutes", 10),
                                          hide_secret=self.cfg.get("hide_encryption_secret", False))
            except Exception as exc:
                self.con = Database.connect()
                if self.welcome is not None:
                    self.welcome.show_error(str(exc))
                return
        for name in ("language_view", "intro"):
            if getattr(self, name) is not None:
                getattr(self, name).destroy()
                setattr(self, name, None)
        self.cfg["intro_seen"] = True
        self.cfg["ui_language"] = self.cfg.get("ui_language") or i18n.Translator.language()
        recursive = choices.get("recursive", True)
        self.cfg["folders"] = [{"path": path, "recursive": recursive} for path in choices["folders"]]
        for key, setting in (("ocr", "ocr"), ("ocr_lang", "ocr_lang"), ("semantic", "semantic_search")):
            if key in choices:
                self.cfg[setting] = choices[key]
        if "auto_scan" in choices:
            self.cfg["auto_scan_seconds"] = 60 if choices["auto_scan"] else 0
        self.cfg.save()
        if self.welcome is not None:
            self._welcome_vars = self.welcome.choices  # le variabili Tk non devono essere liberate da un altro thread
            self.welcome.destroy()
            self.welcome = None
        if protection.get("change"):
            security_ui.restart(self)  # riparte con l'archivio protetto (la cassaforte chiede la passphrase)
            return
        self.reschedule_auto_scan()
        self._update_models()
        self.start_index(force=True)
        self.do_search()
        self.entry.focus_set()

    # ---- costruzione
    def _menu(self, parent, var, values, width, command):
        return SquareOptionMenu(parent, variable=var, values=values, width=width, height=34,
                                 dynamic_resizing=False,
                                 font=font(13), dropdown_font=font(13), fg_color=PAL["card"],
                                 button_color=PAL["card"], button_hover_color=PAL["ghost_hover"],
                                 text_color=PAL["text"], command=command)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Barra dei menu classica: File, Strumenti, Aiuto, con i suggerimenti da tastiera a destra.
        self.command_bar = MenuBar(self)
        self.command_bar.grid(row=0, column=0, sticky="ew", pady=(0, 1))
        self.command_bar.grid_columnconfigure(3, weight=1)
        mod = "⌘" if sys.platform == "darwin" else "Ctrl"
        self.command_bar.add(tr("File"), self._fill_file_menu, self._update_file_menu)
        self.command_bar.add(tr("Strumenti"), self._fill_tools_menu, self._update_tools_menu)
        self.command_bar.add(tr("Aiuto"), self._fill_help_menu)
        ctk.CTkLabel(self.command_bar, text=tr("{mod}+K  Cerca     F3  Occorrenza successiva", mod=mod), font=font(11),
                     text_color=PAL["muted"], anchor="e").grid(row=0, column=3, sticky="e", padx=16)

        self.header = ctk.CTkFrame(self, corner_radius=0, fg_color=PAL["bg"], border_width=0)
        self.header.grid(row=1, column=0, sticky="ew")
        self.header.grid_columnconfigure(1, weight=1)
        logo = ctk.CTkFrame(self.header, fg_color="transparent", corner_radius=0, border_width=0)
        logo.grid(row=0, column=0, padx=(16, 18), pady=10, sticky="w")
        ctk.CTkLabel(logo, text="", image=Brand.symbol(34)).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(logo, text="StillFind", font=font(24, True), text_color=PAL["text"]).pack(side="left")
        ctk.CTkLabel(self.header, text=tr("Cerca dentro i tuoi documenti"), font=font(13), text_color=PAL["muted"]).grid(
            row=0, column=1, sticky="w")
        ctk.CTkLabel(self.header, text=tr("OFFLINE"), font=font(11),
                     text_color=PAL["muted"]).grid(row=0, column=2, padx=16)
        encrypted = self.protection["mode"] != "plain"
        self.lock_button = PreviewAction(self.header, "lock" if encrypted else "unlock",
                                         tr("Archivio cifrato · clic per password e visibilità") if encrypted else
                                         tr("Archivio in chiaro · clic per attivare la cifratura"),
                                         lambda: security_ui.SecretDialog(self))
        self.lock_button.configure(text=tr("CIFRATO") if encrypted else tr("NON CIFRATO"), width=156,
                                   font=font(12, True), text_color=PAL["text"])
        self.lock_button.grid(row=0, column=3, padx=(0, 16))

        self.search_band = ctk.CTkFrame(self, corner_radius=0, fg_color=PAL["card"], border_width=1, border_color=PAL["line"])
        self.search_band.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        self.search_band.grid_columnconfigure(0, weight=1)
        self.qvar = tk.StringVar()
        self.qvar.trace_add("write", lambda *_: self._schedule())
        self.entry = SquareEntry(self.search_band, textvariable=self.qvar, height=40, corner_radius=0, font=font(16),
                                  placeholder_text=tr("Cerca parole, nomi o frasi nei documenti…"), fg_color=PAL["field"],
                                  border_color=PAL["line"], border_width=1, text_color=PAL["text"])
        self.entry.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=(12, 8))
        self.entry.bind("<Return>", self._submit_query)
        self.entry.bind("<Escape>", lambda e: self.qvar.set(""))
        self.entry.bind("<Down>", lambda e: self._focus_list())
        search_button = primary_button(self.search_band, tr("Cerca"), self._submit_query, width=96)
        search_button.configure(height=40)
        search_button.grid(row=0, column=1, padx=(0, 12), pady=(12, 8))
        self.filters = ctk.CTkFrame(self.search_band, fg_color="transparent", border_width=0, corner_radius=0)
        self.filters.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        self.filters.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="filters")
        self.type_choices = self._type_choices([])
        self.type_var = tk.StringVar(value=tr("Tutti i tipi"))
        self.date_var = tk.StringVar(value=ui_text.DATE_CHOICES.label("any"))
        self.folder_var = tk.StringVar(value=tr("Tutte le cartelle"))
        self.where_var = tk.StringVar(value=ui_text.WHERE_CHOICES.label("all"))
        menus = ((tr("Tipo"), self.type_var, self.type_choices.labels(), lambda _: self.do_search()),
                 (tr("Data"), self.date_var, ui_text.DATE_CHOICES.labels(), self._on_date_choice),
                 (tr("Cartella"), self.folder_var, [tr("Tutte le cartelle")], self._on_folder_choice),
                 (tr("Cerca in"), self.where_var, ui_text.WHERE_CHOICES.labels(), lambda _: self.do_search()))
        controls = []
        for column, (label, variable, values, command) in enumerate(menus):
            ctk.CTkLabel(self.filters, text=label, anchor="w", font=font(11), text_color=PAL["muted"]).grid(
                row=0, column=column, sticky="ew", padx=(0, 8 if column < 3 else 0))
            menu = self._menu(self.filters, variable, values, 150, command)
            menu.grid(row=1, column=column, sticky="ew", padx=(0, 8 if column < 3 else 0))
            controls.append(menu)
        self.type_menu, self.date_menu, self.folder_menu, self.where_menu = controls

        # Un divisore reale: i due pannelli si possono allargare con il mouse.
        self.split = tk.PanedWindow(self, orient="horizontal", sashwidth=7, sashpad=0, showhandle=False,
                                   borderwidth=0, relief="flat", background=col("line"), opaqueresize=True)
        self.split.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 10))
        self.results_panel = res = card(self.split)
        self.preview_panel = pv = card(self.split)
        self.split.add(res, minsize=340, width=480, stretch="always")
        self.split.add(pv, minsize=440, width=700, stretch="always")
        res.grid_columnconfigure(0, weight=1)
        res.grid_rowconfigure(2, weight=1)
        rh = ctk.CTkFrame(res, fg_color="transparent", border_width=0)
        rh.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 6))
        rh.grid_columnconfigure(0, weight=1)
        self.res_head = ctk.CTkLabel(rh, text=tr("Documenti"), font=font(14, True), text_color=PAL["text"],
                                     anchor="w", justify="left", width=0, wraplength=220)
        self.res_head.grid(row=0, column=0, sticky="ew")
        rh.bind("<Configure>", lambda e: self.res_head.configure(wraplength=max(120, e.width - 156)))
        self.order_var = tk.StringVar(value=ui_text.ORDER_CHOICES.label("rank"))
        om = self.order_menu = self._menu(rh, self.order_var, ui_text.ORDER_CHOICES.labels(), 140,
                                          lambda _: self.do_search())
        om.configure(height=28, font=font(11), dropdown_font=font(12))
        om.grid(row=0, column=1, padx=(8, 0))
        ctk.CTkFrame(res, height=1, corner_radius=0, border_width=0, fg_color=PAL["line"]).grid(row=1, column=0, sticky="ew", padx=1)
        self.results = ResultList(res, self._on_select, self.open_selected, self._popup, self.copy_path)
        self.results.grid(row=2, column=0, sticky="nsew", padx=1, pady=(0, 1))
        self.results.on_more, self.results.on_occurrence = self._toggle_occurrences, self._open_occurrence
        self.empty = ctk.CTkLabel(res, text="", font=font(14), text_color=PAL["muted"], fg_color=PAL["card"],
                                  justify="center", wraplength=300)
        self.empty.bind("<Button-1>", self._apply_hint)
        self.empty.place(relx=0.5, rely=0.55, anchor="center")
        res.bind("<Configure>", lambda e: self.empty.configure(wraplength=max(180, e.width - 48)))

        pv.grid_columnconfigure(0, weight=1)
        pv.grid_rowconfigure(5, weight=1)
        ph = ctk.CTkFrame(pv, fg_color="transparent", border_width=0)
        ph.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        ph.grid_columnconfigure(1, weight=1)
        self.pv_badge = EmbeddingBadge(ph)   # stato dei vettori del documento mostrato
        self.pv_badge.grid(row=0, column=0, padx=(0, 8))
        self.pv_badge.grid_remove()
        self.pv_title = ctk.CTkLabel(ph, text=tr("Anteprima"), anchor="w", font=font(15, True), text_color=PAL["text"],
                                     wraplength=340, justify="left")
        self.pv_title.grid(row=0, column=1, sticky="ew")
        self.btn_back = ghost_button(ph, tr("Indietro"), self._back, width=76)
        self.btn_back.grid(row=0, column=2, padx=(8, 0))
        self.btn_back.grid_remove()
        self.pv_path = ctk.CTkLabel(pv, text=tr("Seleziona un risultato per leggerne il contenuto"), anchor="w",
                                    font=font(11), text_color=PAL["muted"], wraplength=380, justify="left")
        self.pv_path.grid(row=1, column=0, sticky="ew", padx=12)
        ph.bind("<Configure>", lambda e: self.pv_title.configure(wraplength=max(180, e.width - 130)))
        pv.bind("<Configure>", lambda e: self.pv_path.configure(wraplength=max(180, e.width - 28)))
        pb = ctk.CTkFrame(pv, fg_color="transparent", border_width=0)
        pb.grid(row=2, column=0, sticky="ew", padx=12, pady=8)
        pb.grid_columnconfigure(1, weight=1)
        tools = ctk.CTkFrame(pb, fg_color="transparent", border_width=0)
        tools.grid(row=0, column=0, sticky="w")
        for icon, label, command in (
            ("open", tr("Apri il file"), self.open_selected),
            ("folder", tr("Mostra nella cartella"), self.reveal_selected),
            ("copy", tr("Copia testo"), self.copy_text),
            ("redact", tr("Oscura il testo in una copia…"), lambda: RedactDialog(self)),
        ):
            PreviewAction(tools, icon, label, command).pack(side="left", padx=(0, 6))
        hits = ctk.CTkFrame(pb, fg_color="transparent", border_width=0)
        hits.grid(row=0, column=1, sticky="e")
        self.hit_count = ctk.CTkLabel(hits, text="", font=font(11), text_color=PAL["muted"], anchor="e")
        self.hit_count.grid(row=0, column=0, padx=(0, 8))
        self.btn_hit_prev = ghost_button(hits, "<", lambda: self.step_hit(-1), width=30)
        self.btn_hit_prev.grid(row=0, column=1)
        self.btn_hit_next = ghost_button(hits, ">", lambda: self.step_hit(1), width=30)
        self.btn_hit_next.grid(row=0, column=2, padx=(4, 0))
        self.pv_mode = IdSelector(pv, ((self.MODE_HITS, tr("Occorrenze")), (self.MODE_FULL, tr("Testo completo")),
                                       (self.MODE_LINKS, tr("Collegati"))), command=self._on_mode,
                                  font=font(12), height=30)
        self.pv_mode.set(self.MODE_HITS)
        self.pv_mode.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))
        ctk.CTkFrame(pv, height=1, corner_radius=0, border_width=0, fg_color=PAL["line"]).grid(row=4, column=0, sticky="ew", padx=1)
        self.prev = ctk.CTkTextbox(pv, wrap="word", corner_radius=0, fg_color=PAL["field"], font=font(13),
                                   text_color=PAL["text"], border_width=0)
        self.prev.grid(row=5, column=0, sticky="nsew", padx=1, pady=(0, 1))
        self.prev._textbox.configure(padx=16, pady=12)  # il testo non deve toccare il bordo del riquadro
        self.prev.configure(state="disabled")
        self.links = RelationsPane(pv, self)
        self.links.grid(row=5, column=0, sticky="nsew", padx=1, pady=(0, 1))
        self.links.grid_remove()

        self.footer = ctk.CTkFrame(self, fg_color=PAL["card"], corner_radius=0, border_width=1, border_color=PAL["line"])
        self.footer.grid(row=4, column=0, sticky="ew")
        self.footer.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(self.footer, text="", anchor="w", justify="left", font=font(12),
                                   text_color=PAL["text"], wraplength=900)
        self.status.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 2))
        self.footer.bind("<Configure>", lambda e: self.status.configure(wraplength=max(300, e.width - 32)))
        # Avanzamento: percentuale in grassetto, barra rettangolare sempre visibile, dettaglio e «Ferma».
        meter = ctk.CTkFrame(self.footer, fg_color="transparent", border_width=0)
        meter.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 6))
        meter.grid_columnconfigure(1, weight=1)
        # Spazio riservato a testo, dettaglio e «Ferma»: la barra non cambia larghezza quando cambiano o compaiono.
        meter.grid_columnconfigure(0, minsize=250)
        meter.grid_columnconfigure(2, minsize=200)
        meter.grid_columnconfigure(3, minsize=102)
        self.meter_caption = ctk.CTkLabel(meter, text="", font=font(15, True), text_color=PAL["text"], anchor="w")
        self.meter_caption.grid(row=0, column=0, sticky="w")
        self.progress = ctk.CTkProgressBar(meter, mode="determinate", height=16, corner_radius=0,
                                           progress_color=PAL["accent"])
        self.progress.set(0)
        self.progress.grid(row=0, column=1, sticky="ew", padx=14)
        self.meter_detail = ctk.CTkLabel(meter, text="", font=font(12), text_color=PAL["muted"], anchor="e")
        self.meter_detail.grid(row=0, column=2, sticky="e")
        self.btn_stop = ghost_button(meter, tr("■  Ferma"), self.stop_index, width=88)
        self.btn_stop.configure(height=28, font=font(12, True))
        self._prog_on, self._prog_mode = False, ""
        st = ctk.CTkFrame(self.footer, fg_color="transparent", border_width=0)
        st.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        st.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(st, text=tr("ARCHIVIO LOCALE"), font=font(10, True), text_color=PAL["muted"]).grid(row=0, column=0, sticky="w")
        self.btn_due = ghost_button(st, tr("Scadenze"), lambda: DocumentList(self))
        self.btn_diag = ghost_button(st, "", lambda: DiagnosticsDialog(self))
        self.btn_diag.configure(height=26, font=font(11))
        # Riquadro sempre visibile: quale modello usa l'app (significato e OCR), con il suo suggerimento.
        self.models_panel = ModelsPanel(st)
        self.models_panel.grid(row=0, column=5, sticky="e", padx=(14, 0))
        self._models_sig = None
        self._update_models()

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label=tr("Apri"), command=self.open_selected)
        self.menu.add_command(label=tr("Mostra nella cartella"), command=self.reveal_selected)
        self.menu.add_command(label=tr("Copia percorso"), command=self.copy_path)
        self.menu.add_command(label=tr("Confronta versioni"), command=self.compare_selected)
        self.menu.add_command(label=tr("Oscura copia…"), command=lambda: RedactDialog(self))
        self.menu.add_separator()
        self.menu.add_command(label=tr("Dimentica questo file…"), command=self._forget_result)
        self.menu.add_command(label=tr("Dimentica la cartella che lo contiene…"),
                              command=lambda: self._forget_result(folder=True))
        for seq in ("<Control-k>", "<Command-k>", "<Control-f>", "<Command-f>"):
            self.bind(seq, self._focus_search)
        for seq in ("<F3>", "<Control-g>", "<Command-g>"):  # occorrenza successiva / precedente nell'anteprima
            self.bind(seq, lambda e: self.step_hit(1))
        for seq in ("<Shift-F3>", "<Control-G>", "<Command-G>"):
            self.bind(seq, lambda e: self.step_hit(-1))
        for seq in ("<Control-comma>", "<Command-comma>"):
            self.bind(seq, lambda e: (self._window_action("settings"), "break")[1])
        self.bind("<F1>", lambda e: (self.show_help(), "break")[1])
        self.entry.focus_set()
        self._refresh_filters()
        self.do_search()

    # ---- barra dei menu
    def _fill_file_menu(self, m):
        mod = "⌘" if sys.platform == "darwin" else "Ctrl"
        m.add_command(label=tr("Cerca nuovi documenti adesso"), command=self.start_index)
        m.add_command(label=tr("Metti in pausa l’indicizzazione"), command=self._toggle_pause)
        m.add_command(label=tr("Esporta risultati in CSV…"), command=self.export_results)
        m.add_separator()
        m.add_command(label=tr("Impostazioni…"), accelerator=f"{mod}+,", command=lambda: self._window_action("settings"))
        m.add_command(label=tr("Blocca cassaforte"), command=lambda: security_ui.restart(self))
        m.add_separator()
        m.add_command(label=tr("Esci"), command=lambda: self._window_action("quit"))

    def _update_file_menu(self, m):
        st = self._state or agent.WorkerMonitor.read_status(self.con)
        m.entryconfigure(1, label=tr("Riprendi l’indicizzazione") if st.get("manual_pause") else tr("Metti in pausa l’indicizzazione"),
                         state="normal" if self.cfg["folders"] else "disabled")
        m.entryconfigure(0, state="normal" if self.cfg["folders"] else "disabled")
        m.entryconfigure(2, state="normal" if self.results.results else "disabled")
        m.entryconfigure(5, state="normal" if self.protection["mode"] == "vault" else "disabled")

    def _toggle_pause(self):
        st = self._state or agent.WorkerMonitor.read_status(self.con)
        self._window_action("resume" if st.get("manual_pause") else "pause")

    def _fill_tools_menu(self, m):
        m.add_command(label=tr("Cronologia e ricerche salvate…"), command=lambda: LibraryDialog(self))
        m.add_command(label=tr("Scadenze documenti…"), command=lambda: DocumentList(self))
        m.add_command(label=tr("Mappa dei dati personali (Audit)…"), command=lambda: AuditDialog(self))
        m.add_command(label=tr("Oscura copia del documento…"), command=lambda: RedactDialog(self))
        m.add_command(label=tr("Confronta due versioni selezionate…"), command=self.compare_selected)
        m.add_command(label=tr("Documenti duplicati…"), command=lambda: DuplicatesDialog(self))
        m.add_separator()
        m.add_command(label=tr("Dimentica una cartella…"), command=self._forget_folder)
        m.add_command(label=tr("Stato e diagnostica dell’archivio…"), command=lambda: DiagnosticsDialog(self))

    def _update_tools_menu(self, m):
        m.entryconfigure(3, state="normal" if self._current() else "disabled")
        m.entryconfigure(4, state="normal" if len(self._selected_rows()) == 2 else "disabled")
        m.entryconfigure(7, state="normal" if self.cfg["folders"] else "disabled")

    def _fill_help_menu(self, m):
        m.add_command(label=tr("Guida completa di StillFind…"), accelerator="F1", command=self.show_help)
        m.add_command(label=tr("Sintassi rapida di ricerca…"), command=lambda: self.show_help("search"))
        m.add_separator()
        m.add_command(label=tr("Informazioni su StillFind…"), command=lambda: self._window_action("about"))

    def _focus_search(self, event=None):
        if self.welcome is not None:
            self.welcome.focus_first()
        else:
            self.entry.focus_set()
        return "break"

    def show_help(self, chapter: str | None = None):
        if isinstance(HelpDialog, type):
            existing = next((w for w in self.winfo_children() if isinstance(w, HelpDialog) and w.winfo_exists()), None)
            if existing is not None:
                if chapter:
                    existing.select_chapter(chapter)
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return existing
        return HelpDialog(self, chapter=chapter) if chapter else HelpDialog(self)

    # ---- aspetto (Tk classici che non accettano coppie chiaro/scuro)
    def restyle(self):
        self.split.configure(background=col("line"))
        self.command_bar.restyle()
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("Cerca.Treeview", background=col("card"), fieldbackground=col("card"),
                     foreground=col("text"), rowheight=34, borderwidth=0, relief="flat", font=("", 13))
        st.configure("Cerca.Treeview.Heading", background=col("card"), foreground=col("muted"),
                     relief="flat", borderwidth=0, font=("", 12, "bold"), padding=(8, 8))
        st.map("Cerca.Treeview", background=[("selected", col("sel"))], foreground=[("selected", col("text"))])
        st.map("Cerca.Treeview.Heading", background=[("active", col("card"))])
        st.layout("Cerca.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # niente bordo
        t = self.prev._textbox
        t.tag_config("hit", background=col("hit_bg"), foreground=col("hit_fg"))
        t.tag_config("current", background=col("hit_now"), foreground=col("hit_fg"))
        t.tag_raise("current")
        t.tag_config("dim", foreground=col("muted"))
        t.tag_config("title", foreground=col("text"))
        t.tag_config("err", foreground=col("err"))
        self.empty.configure(fg_color=PAL["card"])
        self.results.restyle()
        for child in self.winfo_children():
            if hasattr(child, "restyle") and child.winfo_exists():
                child.restyle()

    # ---- filtri
    def _submit_query(self, event=None):
        self._history_signature = SearchCriteria.of(self)
        SearchCriteria.record(self)
        self.do_search(force=True)
        return "break"

    def _schedule(self):
        if self._debounce:
            self.after_cancel(self._debounce)
        self._debounce = self.after(250, self.do_search)

    def _roots(self) -> list[str]:
        return [f["path"] for f in self.cfg["folders"]]

    def _idle_status(self) -> str:
        n = self.con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        return trn("{n} documento pronto da cercare", "{n} documenti pronti da cercare", n) if n else tr("Nessun documento ancora")

    def _refresh_filters(self):
        exts = [r[0] for r in self.con.execute("SELECT DISTINCT ext FROM docs")]
        chosen = self.type_choices.id(self.type_var.get(), "all")
        self.type_choices = self._type_choices(exts)
        self.type_menu.configure(values=self.type_choices.labels())
        self.type_var.set(self.type_choices.label(chosen if chosen in self.type_choices.ids else "all"))
        err_n = self.con.execute("SELECT COUNT(*) FROM docs WHERE error != ''").fetchone()[0]
        if err_n:
            self.btn_diag.configure(text=trn("{n} file non letto  ·  perché?", "{n} file non letti  ·  perché?", err_n))
            self.btn_diag.grid(row=0, column=1, padx=(10, 0))
        else:
            self.btn_diag.grid_forget()
        count = RelationGraph(self.con).upcoming_count() if self.cfg.get("deadline_alerts", True) else 0
        if count:
            self.btn_due.configure(text=tr("Scadenze entro 30 giorni · {n} doc.", n=count))
            self.btn_due.grid(row=0, column=4, padx=6)
        else:
            self.btn_due.grid_remove()
        self._refresh_folders()
        if not self.indexing:
            self.status.configure(text=self._idle_status())
            self._update_meter()

    def _refresh_folders(self):
        """Menu «Cartella»: le cartelle scelte e le loro sottocartelle dirette (le altre con `in:Nome`)."""
        rows = self.con.execute("SELECT path, parent FROM dirs ORDER BY path COLLATE NOCASE").fetchall()
        if rows == self._dirs_sig:
            return
        self._dirs_sig = rows
        present = {p for p, _ in rows}
        roots = [p for p, par in rows if par is None or par not in present]
        entries: dict[str, str] = {}

        def unique(label: str) -> str:
            n, out = 2, label
            while out in entries:
                out, n = f"{label} ({n})", n + 1
            return out

        for root in roots:
            base = os.path.basename(root.rstrip("/\\")) or root
            entries[unique(base)] = root
            for p, par in rows:
                if par == root and len(entries) < 80:
                    entries[unique(f"{base} › {os.path.basename(p.rstrip('/\\\\')) or p}")] = p
        self._folder_map = entries
        self.folder_menu.configure(values=[tr("Tutte le cartelle")] + list(entries))
        current = next((k for k, v in entries.items() if v == self.folder_filter), None)
        if current is None:
            self.folder_filter = None
        self.folder_var.set(current or tr("Tutte le cartelle"))

    def _on_folder_choice(self, label: str):
        self.folder_filter = self._folder_map.get(label)
        self.do_search()

    def _where(self) -> str:
        return ui_text.WHERE_CHOICES.id(self.where_var.get(), WHERE_ALL)

    def _date_bounds(self) -> tuple[float | None, float | None]:
        """(dopo, prima) del filtro «Data»: un periodo scelto a mano oppure una delle scelte rapide."""
        if self._date_id() == "range":
            return self._date_custom[0], self._date_custom[1]
        return ui_text.date_after(self._date_id()), None

    def _date_id(self) -> str:
        """Identificatore della scelta nel menu «Data»; «range» se è un periodo scritto a mano."""
        if self._date_custom and self.date_var.get() == self._date_custom[2]:
            return "range"
        return ui_text.DATE_CHOICES.id(self.date_var.get(), "any")

    def _type_choices(self, exts) -> ui_text.Choices:
        names = dict(ui_text.CATEGORY_NAMES.items)
        return ui_text.Choices([("all", mark("Tutti i tipi"))] + [(cid, names[cid]) for cid in ui_text.present_categories(exts)])

    def _on_date_choice(self, choice: str):
        if ui_text.DATE_CHOICES.id(choice) != ui_text.CUSTOM_DATE:
            self.do_search()
            return
        prev = self._date_custom
        # il menu mostra già «Scegli le date…»: si rimette la scelta precedente finché non si conferma
        self.date_var.set(prev[2] if prev else ui_text.DATE_CHOICES.label("any"))
        DateRangeDialog(self, self._apply_date_range, prev[3] if prev else "", prev[4] if prev else "")

    def _apply_date_range(self, after, before, label, start_text, end_text):
        self._date_custom = (after, before, label, start_text, end_text)
        self.date_var.set(label)  # il menu mostra il periodo scelto, per esempio «12/03/2025 – 20/04/2025»
        self.do_search()

    def _type_exts(self):
        exts = ui_text.category_exts(self.type_choices.id(self.type_var.get(), "all"))
        return list(exts) or None

    # ---- ricerca
    def _order_id(self) -> str:
        return ui_text.ORDER_CHOICES.id(self.order_var.get(), "rank")

    def _show_empty(self, text: str, hint: str | None = None):
        self._hint = hint
        self.empty.configure(text=text, cursor="hand2" if hint else "")
        self.empty.place(relx=0.5, rely=0.55, anchor="center")
        self.empty.lift()

    def do_search(self, force: bool = False):
        """Avvia la ricerca in un thread, così la finestra non si blocca mai. Mentre si digita, sotto le 3
        lettere non si cerca (sarebbe troppo lento e inutile): si cerca premendo Invio."""
        if self._debounce is not None:
            self.after_cancel(self._debounce)
            self._debounce = None
        q = self.qvar.get().strip()
        if force:
            self._forced_q = q
        short = bool(q) and len(re.sub(r"\W", "", q)) < 3 and q != self._forced_q
        run_q = "" if short else q
        exts = self._type_exts()
        after, before = self._date_bounds()
        where = self._where()
        order = self._order_id()
        under = self.folder_filter
        filtered = bool(exts or after or before or under or (run_q and where != WHERE_ALL))
        self._search_seq += 1
        seq = self._search_seq
        prev = self._search_con
        if prev is not None:
            try:
                prev.interrupt()  # annulla la ricerca precedente, ancora in corso
            except prev.Error:
                pass
        if short:
            self.res_head.configure(text=tr("Scrivi almeno 3 lettere, oppure premi Invio"))
        elif run_q:
            self.res_head.configure(text=tr("Sto cercando…"))

        def work():
            con = ArchiveProtection.connection(config.DB_PATH)  # connessione propria: quella della finestra è sua
            self._search_con = con
            try:
                if run_q:
                    res = self._search_rows(con, run_q, exts, order, under, after, before, where)
                    SearchEngine(con).add_pages(res)  # «Pag. N» per i passaggi trovati nei PDF
                elif filtered:
                    flt = {k: v for k, v in (("after", after), ("before", before)) if v}
                    res = SearchEngine(con).list_documents(exts, order, under=under, flt=flt or None)
                else:
                    res = SearchEngine(con).list_documents(None, "document_date" if order == "document_date" else "date", RECENT_COUNT)
                SearchEngine(con).add_embedding(res, self.cfg)  # stato dei vettori di ogni documento mostrato
                hint = SearchEngine(con).suggest(run_q) if run_q and not res else None
                self.msgs.put(("results", (seq, run_q, filtered, short, res, hint)))
            except con.Error as ex:
                if "interrupt" not in str(ex).lower():  # «interrotta» = ne è partita una più recente
                    self.msgs.put(("results", (seq, run_q, filtered, short, [], None)))
            finally:
                if self._search_con is con:
                    self._search_con = None
                con.close()

        threading.Thread(target=work, daemon=True).start()

    def _get_embedder(self):
        """Il modello per la ricerca per significato, caricato (e controllato) solo la prima volta che serve.
        None se la funzione è spenta o il modello non si può usare: la ricerca resta per parole."""
        if self._embedder_failed or not SemanticIndex.available(self.cfg):
            return None
        with self._embedder_lock:
            if self._embedder is None and not self._embedder_failed:
                try:
                    from stillfind.semantic.embedder import Embedder
                    self._embedder = Embedder()
                except Exception:  # modello rifiutato, libreria mancante…: solo parole
                    self._embedder_failed = True
                self.msgs.put(("models", None))  # il piè di pagina mostra com'è andato il controllo
            return self._embedder

    def _search_rows(self, con, q, exts, order, under, after, before=None, where="all") -> list[dict]:
        use_meaning = order == "rank" and where != WHERE_NAME and Query.parse(q).plain_text()
        embedder = self._get_embedder() if use_meaning else None
        if embedder is None:
            return SearchEngine(con).search(q, exts, order, under=under, after=after, before=before, where=where)
        return SemanticSearch(con, embedder).hybrid_search(q, ext=exts, order=order, under=under, after=after,
                                      before=before, where=where, min_score=float(self.cfg.get("semantic_min_score", SemanticSearch.MIN_SCORE)))

    def _apply_results(self, q: str, filtered: bool, short: bool, res: list[dict], hint: str | None):
        if self._history_job:
            self.after_cancel(self._history_job)
            self._history_job = None
        current = SearchCriteria.of(self)
        if (q or filtered) and not short and current != self._history_signature:
            def remember():
                self._history_job = None
                if current == SearchCriteria.of(self) and current != self._history_signature:
                    self._history_signature = current
                    SearchCriteria.record(self)
            self._history_job = self.after(1600, remember)
        self.rows = {str(i): r for i, r in enumerate(res)}
        n = len(res)
        if q:
            head = trn("{n} risultato", "{n} risultati", n)
            if n >= 500:
                head += " " + tr("(i primi 500)")
        elif short:
            head = tr("Scrivi almeno 3 lettere, oppure premi Invio")
        elif filtered:
            head = trn("{n} documento", "{n} documenti", n)
        else:
            head = tr("Documenti recenti") if n else tr("Documenti")
        self.res_head.configure(text=head)
        roots = self._roots()
        for r in res:  # solo le ricerche per parole nel testo hanno «altre occorrenze» da mostrare
            r["expandable"] = bool(q) and "offset" not in r and not r.get("error") and HIT_START in r.get("snippet", "")
        self.results.show(res, [self._block(r, q, roots) for r in res])
        if res:
            self.empty.place_forget()
            self._hint = None
            self.results.select(0)
        else:
            self._clear_preview()
            self._explain_empty(q, filtered, hint)

    def _explain_empty(self, q: str, filtered: bool, hint: str | None = None):
        if q:
            text = (tr("Nessun risultato per «{q}» con i filtri scelti.\n\nProva a togliere qualche filtro in alto.", q=q)
                    if filtered else tr("Nessun risultato per «{q}»\n\nProva con meno parole o controlla l'ortografia.", q=q))
            if hint:
                text = tr("Nessun risultato per «{q}»\n\nIntendevi «{hint}»?\n(clicca qui per cercarlo)", q=q, hint=hint)
            self._show_empty(text, hint)
        elif filtered:
            self._show_empty(tr("Nessun documento corrisponde ai filtri scelti."))
        elif self.indexing:
            self._show_empty(tr("Sto leggendo i tuoi documenti…\nTra poco potrai cercarli."))
        elif self.con.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 0:
            self._show_empty(tr("Non ho trovato documenti nelle cartelle scelte.\nControlla le cartelle da Impostazioni."))
        else:
            self._show_empty(tr("Scrivi qui sopra cosa cerchi."))

    def _block(self, r: dict, q: str, roots: list[str]) -> list[tuple[str, tuple]]:
        """Un risultato: titolo, cartella e data, poi la frase in cui compare la parola cercata."""
        where = ui_text.friendly_folder(r["folder"], roots)
        stamp = ui_text.relative_date(r["mtime"])
        if self._order_id() == "document_date":
            stamp = (tr("Documento del {date}", date=self._document_date(r["document_date"]))
                     if r.get("document_date") else tr("Modifica del file: {date}", date=stamp))
        segs: list[tuple[str, tuple]] = [
            (r["ext"].upper() + "  ", ("icon",)), (r["name"] + "\n", ("title",)),
            (f"{where}  ·  {stamp}\n", ("meta",))]
        if r.get("error") and not ReadProblems.is_partial(r["error"]):
            segs.append((tr("Non sono riuscito a leggere il contenuto: il file si trova solo per nome.") + "\n", ("snip", "warn")))
        elif q:
            parts = DocumentPreview.snippet_parts(r.get("snippet", ""))
            page = r["location"] + " — " if r.get("location") else ""
            if parts:
                segs.append((page, ("snip", "warn")))
                for text, tag in parts:
                    segs.append((text.rstrip("\n").replace("\n", " ") + ("\n" if text.endswith("\n") else ""), ("snip", "hit") if tag == "hit" else ("snip",)))
            elif r.get("passage_start") is not None:
                segs.append((page + r["snippet"].replace("\n", " ") + "\n", ("snip",)))
            elif r.get("semantic"):  # trovato per significato: non ci sono parole da evidenziare, si mostra il passaggio
                segs.append((f"{page}≈ {r['snippet']}\n", ("snip",)))
            else:
                segs.append((tr("Trovato nel nome del file") + "\n", ("snip", "warn")))
        segs.append(("\n", ("gap",)))
        return segs

    @staticmethod
    def _document_date(iso: str) -> str:
        """Data del documento (AAAA-MM-GG) nel formato numerico della lingua."""
        try:
            year, month, day = (int(x) for x in iso.split("-"))
        except ValueError:
            return iso
        return i18n.Formats.numeric_date(year, month, day)

    def _apply_hint(self, _event=None):
        if self._hint:
            self.qvar.set(self._hint)  # la modifica fa partire la ricerca

    def export_results(self):
        """Salva in CSV i risultati dell'ultima ricerca."""
        rows = list(self.results.results)
        if not rows:
            messagebox.showinfo(tr("Salva i risultati"), tr("Non ci sono risultati da salvare."), parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile=tr("risultati.csv"),
                                            filetypes=[(tr("CSV (si apre con Excel)"), "*.csv")])
        if path:
            try:
                n = CsvExporter(path).write(rows)
                self.status.configure(text=tr("{n} risultati salvati in {path}", n=n, path=system.short_path(path)))
            except OSError as ex:
                messagebox.showerror(tr("Salva i risultati"), tr("Non riesco a salvare il file: {error}", error=ex), parent=self)

    def _focus_list(self):
        self.results.focus_list()
        return "break"

    # ---- anteprima
    def _set_preview(self, parts, goto: int | None = None):
        self.prev.configure(state="normal")
        self.prev.delete("1.0", "end")
        for text, tag in parts:
            self.prev._textbox.insert("end", text, tag)
        self.prev.configure(state="disabled")
        self._index_hits()
        if goto is not None and self._hit_ranges:
            self._goto_hit(min(goto, len(self._hit_ranges) - 1))

    def _clear_preview(self):
        self._preview_seq += 1
        self._linked_row = self._anchor = None
        self._back_stack.clear()
        self.btn_back.grid_remove()
        self.links.reset()
        self.links.grid_remove()
        self.prev.grid()
        self._pv_layout = self._pv_content = self._pv_hits_parts = self._pv_full_parts = None
        self.pv_title.configure(text=tr("Anteprima"))
        self.pv_badge.set_state(None)
        self.pv_path.configure(text=tr("Seleziona un risultato per leggerne il contenuto"))
        self._set_preview([])

    # ---- occorrenze: contatore, ‹ ›, vista a brani o testo completo
    def _index_hits(self):
        """Rilegge dove sono le parole evidenziate nel riquadro e aggiorna contatore e pulsanti."""
        t = self.prev._textbox
        t.tag_remove("current", "1.0", "end")
        r = t.tag_ranges("hit")
        self._hit_ranges = [(str(r[i]), str(r[i + 1])) for i in range(0, len(r) - 1, 2)]
        self._hit_i = -1
        n = len(self._hit_ranges)
        state = "normal" if n else "disabled"
        self.btn_hit_prev.configure(state=state)
        self.btn_hit_next.configure(state=state)
        self.hit_count.configure(text=trn("{n} occorrenza", "{n} occorrenze", n) if n else "")

    def _goto_hit(self, i: int):
        if not self._hit_ranges:
            return
        self._hit_i = i % len(self._hit_ranges)
        a, b = self._hit_ranges[self._hit_i]
        t = self.prev._textbox
        t.tag_remove("current", "1.0", "end")
        t.tag_add("current", a, b)
        t.tag_raise("current")
        t.see(a)
        self.hit_count.configure(text=tr("{i} di {n}", i=self._hit_i + 1, n=ui_text.thousands(len(self._hit_ranges))))

    def step_hit(self, delta: int):
        """Occorrenza successiva (+1) o precedente (-1); dopo l'ultima si riparte dalla prima."""
        if not self._hit_ranges:
            return "break"
        self._goto_hit(0 if self._hit_i < 0 and delta > 0 else -1 if self._hit_i < 0 else self._hit_i + delta)
        return "break"

    def _on_mode(self, _value=None):
        self._render_preview()

    def _render_preview(self):
        """Mostra il documento in anteprima secondo la vista scelta (brani o testo completo)."""
        if self.pv_mode.get() == self.MODE_LINKS and self._current():
            self.prev.grid_remove()
            self.links.grid()
            self.hit_count.grid_remove()
            self.btn_hit_prev.grid_remove()
            self.btn_hit_next.grid_remove()
            self.links.load(self._current()["id"], SemanticIndex.available(self.cfg))
            return
        self.links.grid_remove()
        self.prev.grid()
        self.hit_count.grid()
        self.btn_hit_prev.grid()
        self.btn_hit_next.grid()
        if self._pv_content is None:
            return
        if self.pv_mode.get() == self.MODE_FULL:
            if self._pv_full_parts is None:
                self._set_preview([(tr("Preparo il testo completo…"), "dim")])
                self._prepare_full_text()
                return
            goto, self._pending_hit = (self._pending_hit or 0), None  # l'occorrenza scelta nell'elenco, o la prima
            self._set_preview(self._pv_full_parts, goto=goto)
        else:
            self._set_preview(self._pv_hits_parts or [], goto=0 if self._anchor else None)

    def _prepare_full_text(self):
        if self._pv_full_busy:
            return
        self._pv_full_busy = True
        layout = self._pv_layout
        seq, content, terms, anchor = self._preview_seq, self._pv_content, self._pv_terms, self._anchor

        def work():
            try:
                parts = (DocumentPreview.anchored_parts(content, *anchor, full=True, layout=layout) if anchor else
                         DocumentPreview.full_text_parts(content, terms, layout=layout))
            except Exception as ex:
                parts = [(tr("Non riesco a mostrare il testo: {error}", error=ex), "err")]
            self.msgs.put(("preview_full", (seq, parts)))

        threading.Thread(target=work, daemon=True).start()

    # ---- «Altre occorrenze» sotto il risultato
    def _toggle_occurrences(self, i: int):
        if not (0 <= i < len(self.results.results)):
            return
        if self.results.open_items.get(i, False) is not False:  # aperto o in caricamento: si chiude
            self.results.set_open(i, False)
            return
        self.results.set_open(i, None)
        doc_id, terms, seq = self.results.results[i]["id"], Query.parse(self.qvar.get()).terms(), self._search_seq

        def work():
            try:
                content, layout = DocumentPreview.read_document(doc_id)
                items, total = DocumentPreview.occurrences(content, terms, layout=layout)
            except Exception:
                items, total = [], 0
            self.msgs.put(("occurrences", (seq, doc_id, items, total)))

        threading.Thread(target=work, daemon=True).start()

    def _open_occurrence(self, i: int, item: dict):
        """Un clic su una voce dell'elenco: l'anteprima passa al testo completo, a quell'occorrenza."""
        self._pending_hit = item["index"]  # dopo show_preview (che azzera): il testo si sta già caricando
        if self.pv_mode.get() != self.MODE_FULL:
            self.pv_mode.set(self.MODE_FULL)
        self._render_preview()

    def copy_text(self):
        """Copia il testo selezionato nell'anteprima oppure, se non c'è una selezione, tutto il documento."""
        try:
            text = self.prev._textbox.get("sel.first", "sel.last")
        except tk.TclError:
            text = ""
        selected = True
        if not text:
            if not self._pv_content:
                self.status.configure(text=tr("Non c'è testo da copiare: scegli prima un documento."))
                return
            text, selected = self._pv_content.replace("\r\n", "\n"), False
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.configure(text=(tr("Testo selezionato copiato ({n} caratteri)", n=ui_text.thousands(len(text))) if selected
                                    else tr("Testo del documento copiato ({n} caratteri)", n=ui_text.thousands(len(text)))))

    def _on_select(self):
        self._linked_row = None
        self._back_stack.clear()
        self.btn_back.grid_remove()
        self.show_preview()

    def open_indexed(self, doc_id, start=None, end=None):
        row = SearchEngine(self.con).documents_by_id([doc_id]).get(doc_id)
        if row is None:
            self.status.configure(text=tr("Il documento non è più nell’archivio."))
            return
        if self._current():
            self._back_stack.append((self._linked_row, self.pv_mode.get()))
            self.btn_back.grid()
        if start is not None:
            row.update(offset=start, end=end if end is not None else start + 1)
        self._linked_row = row
        self.pv_mode.set(self.MODE_FULL)
        self.show_preview()

    def _back(self):
        if self._back_stack:
            self._linked_row, mode = self._back_stack.pop()
            self.pv_mode.set(mode)
            if not self._back_stack:
                self.btn_back.grid_remove()
            self.show_preview()

    def show_preview(self):
        r = self._current()
        if not r:
            return
        self._preview_seq += 1
        seq = self._preview_seq
        self._pv_layout = self._pv_content = self._pv_hits_parts = self._pv_full_parts = None
        self._pv_full_busy = False
        self._pending_hit = None
        anchor = (r["offset"], r.get("end", r["offset"] + 1)) if r.get("offset") is not None else None
        self._anchor = anchor
        self.links.reset()
        self._render_preview()
        self.pv_title.configure(text=r["name"])
        self._show_embedding_badge(r)
        where = ui_text.friendly_folder(r["folder"], self._roots())
        stamp = (tr("Documento del {date} ({source})", date=self._document_date(r["document_date"]),
                    source=r["date_source"]) if r.get("document_date")
                 else tr("Modifica del file: {date}", date=ui_text.relative_date(r["mtime"])))
        self.pv_path.configure(text=f"{where}  ·  {stamp}"
                                    + ("  ·  " + tr("OCR utilizzato nel documento") if r.get("ocr") else ""))
        if r.get("error") and not ReadProblems.is_partial(r["error"]):
            self._set_preview([(tr("Non sono riuscito a leggere questo file: {error}", error=ReadProblems.display(r["error"])) + "\n", "err"),
                               (tr("Il file si trova comunque cercando il suo nome.") + "\n", "dim")])
            return
        # subito: l'estratto già calcolato da SQLite; poi, in un thread, tutte le occorrenze
        quick = DocumentPreview.snippet_parts(r.get("snippet", ""))
        if quick and r.get("location"):
            quick.insert(0, (r["location"] + "\n", "title"))
        self._set_preview(quick + [("\n" + tr("Carico gli altri punti in cui compare…"), "dim")] if quick
                          else [(tr("Carico l'anteprima…"), "dim")])
        terms = Query.parse(self.qvar.get()).terms()
        doc_id = r["id"]

        def work():
            content = layout = None
            try:
                content, layout = DocumentPreview.read_document(doc_id)
                parts = (DocumentPreview.anchored_parts(content, *anchor, layout=layout) if anchor else
                         DocumentPreview.preview_parts(content, terms, max_n=OCCURRENCES_SHOWN, layout=layout,
                                               preferred=r.get("passage_start")))
            except Exception as ex:
                parts = [(tr("Non riesco a mostrare l'anteprima: {error}", error=ex), "err")]
            self.msgs.put(("preview", (seq, parts, content, terms, layout)))

        threading.Thread(target=work, daemon=True).start()

    # ---- stato dei vettori («significato») del documento
    EMBEDDING_REFRESH_MS = 5000

    def _show_embedding_badge(self, r: dict):
        """Badge dell'anteprima, riletto dall'archivio: lo stato può essere cambiato dopo la ricerca."""
        try:
            state = SearchEngine(self.con).embedding_state(r["id"], self.cfg)
        except self.con.Error:
            state = r.get("embedding")
        r["embedding"] = state
        self.pv_badge.set_state(state)

    def _refresh_embedding_states(self):
        """Mentre il lavoratore calcola i vettori, porta a «attivo» le icone dei documenti in coda già mostrati."""
        if self._closing:
            return
        try:
            rows = self.results.results
            queued = [i for i, r in enumerate(rows) if (r.get("embedding") or {}).get("state") == "pending"]
            if queued:
                states = SemanticIndex(self.con).document_states([rows[i]["id"] for i in queued], self.cfg)
                for i in queued:
                    self.results.set_embedding(i, states.get(rows[i]["id"]))
            current = self._current()
            if current and (self.pv_badge.embedding or {}).get("state") == "pending":
                self._show_embedding_badge(current)
        except (self.con.Error, tk.TclError):
            pass  # archivio occupato o finestra in chiusura: si riprova al prossimo giro
        self.after(self.EMBEDDING_REFRESH_MS, self._refresh_embedding_states)

    # ---- azioni sul file
    def _current(self):
        """Il risultato attivo: con più righe selezionate, l'ultimo cliccato."""
        return self._linked_row or self.results.active_result()

    def _selected_rows(self) -> list[dict]:
        return self.results.selected_results()

    def _popup(self, e):
        n = len(self.results.selected)
        self.menu.entryconfigure(2, label=tr("Copia {n} percorsi", n=n) if n > 1 else tr("Copia percorso"))
        self.menu.entryconfigure(3, state="normal" if n == 2 else "disabled")
        self.menu.tk_popup(e.x_root, e.y_root)

    def _forget_result(self, folder=False):
        row = self.results.active_result()
        if row:
            path = os.path.dirname(row["path"]) if folder else row["path"]
            self._confirm_forget(path, folder)

    def _forget_folder(self):
        path = ForgetFolderDialog(self, tr("Dimentica una cartella dall’archivio")).result
        if path:
            self.start_index(force=True, target=path, forget="folder")

    def _confirm_forget(self, path, folder):
        if messagebox.askyesno(tr("Dimentica dall’archivio"),
                               (tr("Rimuovere dall’indice questa cartella e tutte le sottocartelle?\n\n{path}\n\n"
                                   "Gli originali restano sul disco. Potrai includere di nuovo il percorso "
                                   "dalle opzioni avanzate delle Impostazioni.", path=path) if folder else
                                tr("Rimuovere dall’indice questo file?\n\n{path}\n\n"
                                   "Gli originali restano sul disco. Potrai includere di nuovo il percorso "
                                   "dalle opzioni avanzate delle Impostazioni.", path=path)), parent=self):
            self.start_index(force=True, target=path, forget="folder" if folder else "file")

    def compare_selected(self):
        rows = self._selected_rows()
        if len(rows) == 2:
            CompareDialog(self, rows[0]["id"], rows[1]["id"])

    def open_selected(self):
        r = self._current()
        if r:
            try:
                system.open_file(r["path"])
            except Exception as ex:
                messagebox.showerror("StillFind", tr("Non riesco ad aprire il file:\n{error}", error=ex))

    def reveal_selected(self):
        r = self._current()
        if r:
            system.reveal_file(r["path"])

    def copy_path(self):
        """Copia negli appunti i percorsi dei risultati selezionati, uno per riga."""
        rows = self._selected_rows()
        if rows:
            self.clipboard_clear()
            self.clipboard_append("\n".join(r["path"] for r in rows))
            self.status.configure(text=tr("Percorso copiato negli appunti") if len(rows) == 1
                                  else tr("{n} percorsi copiati negli appunti", n=len(rows)))

    # ---- lettura dei documenti in background
    METER_COLORS = {"done": "ready", "paused": "pending", "stopped": "pending"}

    def _show_progress(self, on: bool, mode: str = "indeterminate", value: float = 0.0, kind: str = ""):
        """`on`: lavoro in corso («Ferma» visibile). La barra c'è sempre: a riposo mostra lo stato dell'archivio."""
        self.progress.configure(progress_color=PAL[self.METER_COLORS.get(kind, "accent")])
        if on and not self._prog_on:
            self.btn_stop.grid(row=0, column=3, padx=(14, 0))
        elif not on and self._prog_on:
            self.btn_stop.grid_remove()
        self._prog_on = on
        if mode != self._prog_mode:
            self.progress.stop()
            self.progress.configure(mode=mode)
            self._prog_mode = mode
            if mode == "indeterminate":
                self.progress.start()
        if mode == "determinate":
            self.progress.set(max(0.0, min(1.0, value)))

    def _set_meter(self, meter: dict):
        self.meter_caption.configure(text=meter["caption"])
        self.meter_detail.configure(text=meter["detail"])
        known = meter["value"] is not None
        self._show_progress(meter["active"], "determinate" if known else "indeterminate",
                            meter["value"] or 0.0, meter["kind"])

    def _update_models(self):
        """Aggiorna il riquadro dei modelli nel piè di pagina; non tocca nulla se lo stato non è cambiato."""
        model = ModelCatalog.status(SemanticIndex.availability(self.cfg), loaded=self._embedder is not None,
                                    failed=self._embedder_failed)
        labels = ui_text.footer_models(model, Tesseract.status(self.cfg))
        if labels != self._models_sig:
            self._models_sig = labels
            self.models_panel.set_state(labels)

    def _update_meter(self, st: dict | None = None, quiet: bool = False):
        """Ridisegna il blocco di avanzamento dallo stato del lavoratore (oppure da quello dell'archivio a riposo)."""
        self._update_models()
        st = st or self._state or agent.WorkerMonitor.read_status(self.con)
        self._set_meter(ui_text.footer_progress(st, st.get("indexed", 0), quiet))

    def stop_index(self):
        self._reindex_pending = None
        self._forget_queue.clear()
        self._interrupt_index()

    def _interrupt_index(self):
        st = agent.WorkerMonitor.read_status(self.con)
        runs = {self._own_run}
        if st["phase"] in agent.WorkerMonitor.ACTIVE:
            runs.add(st["run"])
        for run in runs:
            if run:
                agent.WorkerMonitor.stop_file(run).touch(mode=0o600)
        self.status.configure(text=tr("Mi fermo…"))

    def rebuild_index(self):
        self._dirs_sig = None
        self.folder_filter = None
        self.start_index(force=True, rebuild=True)

    def start_index(self, silent=False, force=False, retry_errors=False, target=None, force_ocr=False, rebuild=False,
                    forget=None):
        if not self.cfg["folders"] and not target and not rebuild:
            if not silent:
                self.status.configure(text=tr("Scegli le cartelle da Impostazioni."))
            return
        st = agent.WorkerMonitor.read_status(self.con)
        req = dict(silent=silent, force=force, retry_errors=retry_errors, target=target,
                   force_ocr=force_ocr, rebuild=rebuild, forget=forget)
        if self._worker is not None or st["phase"] in agent.WorkerMonitor.ACTIVE:
            if force:
                if forget:
                    self._forget_queue.append(req)
                else:
                    self._reindex_pending = req
                if st["phase"] != "forget" and not (self._request or {}).get("forget"):
                    self._interrupt_index()
            return
        if st["manual_pause"] and not force:
            self.status.configure(text=tr("Indicizzazione in pausa"))
            return
        run = uuid.uuid4().hex
        self._last_scan = time.monotonic()
        self._silent = silent
        try:
            self._worker = agent.WorkerLauncher.spawn(from_window=True, run=run, retry_errors=retry_errors, target=target,
                                              force_ocr=force_ocr, rebuild=rebuild, quit_token=self._quit_token, forget=forget)
        except (OSError, RuntimeError) as exc:
            agent.WorkerMonitor.launch_failed(self.con, run, str(exc))
            self.status.configure(text=str(exc))
            return
        self._own_run, self._request = run, req
        self.indexing = True
        if not silent:
            self.status.configure(text=tr("Rimozione dall’archivio…") if forget else tr("Cerco i documenti nelle cartelle…"))
            self._update_meter({"phase": "forget" if forget else "scan", "scanned": 0, "indexed": st.get("indexed", 0)})

    def _sync_worker(self):
        code = self._worker.poll() if self._worker is not None else None
        st = agent.WorkerMonitor.read_status(self.con)
        active = st["phase"] in agent.WorkerMonitor.ACTIVE
        launch_error = None
        if code is not None:
            if self._worker.stdin:
                self._worker.stdin.close()
            if code == agent.WorkerMonitor.BUSY:
                if self._request.get("forget"):
                    self._forget_queue.insert(0, self._request)
                    if active and st["phase"] != "forget":
                        self._interrupt_index()
                elif (self._request["force"] or not active) and self._reindex_pending is None:
                    self._reindex_pending = self._request
            elif code and (st["run"] != self._own_run or st["phase"] != "error"):
                launch_error = tr("Il lavoratore non è partito nel recinto senza rete.")
                if not active:
                    agent.WorkerMonitor.launch_failed(self.con, self._own_run, launch_error)
                    st = self.con.worker_status()
            agent.WorkerMonitor.stop_file(self._own_run).unlink(missing_ok=True)
            self._worker = self._own_run = self._request = None
        previous = self._state
        self._state = st
        self.indexing = active or self._worker is not None
        if not self.indexing and self._forget_queue:
            self.start_index(**self._forget_queue.pop(0))
            return
        if not self.indexing and self._reindex_pending:
            req, self._reindex_pending = self._reindex_pending, None
            self.start_index(**req)
            return
        if launch_error:
            self._update_meter(st)
            self.status.configure(text=launch_error)
            return
        if st == previous and code is None:
            return
        self._update_meter(st, quiet=self._silent and st["run"] == self._own_run)
        if st["phase"] == "forget":
            self.status.configure(text=tr("Rimozione dall’archivio…"))
        elif st.get("forgotten"):
            self._clear_preview()
            self._refresh_filters()
            self.do_search()
            self.status.configure(text=tr("File rimossi dall’archivio · {n}", n=st["removed"]) +
                                       (" · " + tr("Indicizzazione in pausa") if st["manual_pause"] else ""))
        elif st["manual_pause"] and st["phase"] != "error":
            self.status.configure(text=tr("Indicizzazione in pausa"))
        elif active:
            if st.get("paused"):
                self.status.configure(text=tr("In pausa a batteria. Riprendo quando colleghi l’alimentatore."))
            elif st["phase"] == "embed":
                self.status.configure(text=tr("Calcolo del significato · {done} di {total}",
                                              done=st.get("done", 0), total=st.get("total", 0)))
            elif st["phase"] == "index" and st.get("total"):
                self.status.configure(text=tr("Lettura {done} di {total} · {current} · puoi già cercare",
                                              done=min(st["done"] + 1, st["total"]), total=st["total"],
                                              current=st.get("current", "")))
            elif not self._silent or st["run"] != self._own_run:
                self.status.configure(text=tr("Cerco i documenti nelle cartelle… {n} trovati", n=st.get("scanned", 0)))
        elif self._worker is None:
            self._refresh_filters()
            if st["phase"] == "error":
                self.status.configure(text=tr("Lettura interrotta · {error}", error=st["error"]))
            elif st.get("stopped"):
                self.status.configure(text=tr("Lettura interrotta"))
            elif st["phase"] == "done":
                labels = {"added": mark("{n} nuovi"), "updated": mark("{n} modificati"), "moved": mark("{n} spostati"),
                          "removed": mark("{n} non più presenti"), "errors": mark("{n} non letti")}
                details = [tr(labels[k], n=st[k]) for k in labels if st.get(k)]
                if st.get("missing"):
                    details.append(tr("Cartelle non trovate · {folders}", folders=", ".join(st["missing"])))
                self.status.configure(text=" · ".join([self._idle_status(), *details]))
            if previous is not None or st["phase"] == "done":
                self.do_search()

    def reschedule_auto_scan(self):
        if self._auto_scan_job is not None:
            try:
                self.after_cancel(self._auto_scan_job)
            except tk.TclError:
                pass
            self._auto_scan_job = None
        secs = int(self.cfg.get("auto_scan_seconds", 60) or 0)
        if secs > 0:
            self._auto_scan_job = self.after(max(10, secs) * 1000, self._auto_scan)

    def _auto_scan(self):
        """Controllo periodico: documenti aggiunti, modificati, rinominati o tolti."""
        self._auto_scan_job = None
        self.start_index(silent=True)
        self.reschedule_auto_scan()

    def _on_focus(self, e):
        # tornando sull'app (es. dopo aver copiato un file nella cartella) controlla subito
        if e.widget is self and time.monotonic() - self._last_scan > 5:
            self.start_index(silent=True)

    def _poll(self):
        try:
            while not self.msgs.empty():
                kind, data = self.msgs.get_nowait()
                if kind == "tray":
                    self._window_action(data)
                    if self._closing:
                        return
                elif kind == "models":
                    self._update_models()
                elif kind == "results":
                    if data[0] == self._search_seq:
                        self._apply_results(*data[1:])
                elif kind == "preview":
                    seq, parts, content, terms, layout = data
                    if seq == self._preview_seq:
                        self._pv_layout = layout
                        self._pv_hits_parts, self._pv_content, self._pv_terms = parts, content, terms
                        self._render_preview() if content is not None else self._set_preview(parts)
                elif kind == "occurrences":
                    seq, doc_id, items, total = data
                    i = next((k for k, r in enumerate(self.results.results) if r["id"] == doc_id), None)
                    if seq == self._search_seq and i is not None and self.results.open_items.get(i, False) is None:
                        if items:
                            self.results.set_open(i, (items, total))
                        else:
                            self.results.set_open(i, False)
                            self.status.configure(text=tr("Non trovo altre occorrenze in questo documento."))
                elif kind == "preview_full":
                    seq, parts = data
                    if seq == self._preview_seq:
                        self._pv_full_busy = False
                        self._pv_full_parts = parts
                        if self.pv_mode.get() == self.MODE_FULL:
                            self._render_preview()
                elif kind == "shortcut_error":
                    self.status.configure(text=tr("Scorciatoia globale non disponibile · {error}", error=data))
        except Exception as ex:  # un errore qui non deve fermare per sempre l'aggiornamento
            try:
                self.status.configure(text=tr("Errore interno: {error}", error=ex))
            except tk.TclError:
                pass
        self.after(80, self._poll)

    def _close(self):
        if self._closing:
            return
        self._closing = True
        ArchiveProtection.forget()
        security_ui.SecretControls.clear_session_views(self)
        self.withdraw()
        for child in self.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.withdraw()
        self._shortcut.close()
        self._tray.close()
        self._reindex_pending = None
        if self._worker is not None:
            agent.WorkerMonitor.stop_file(self._own_run).touch(mode=0o600)
            try:
                self._worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._worker.kill()
                self._worker.wait()
            if self._worker.stdin:
                self._worker.stdin.close()
            agent.WorkerMonitor.stop_file(self._own_run).unlink(missing_ok=True)
        try:
            self.con.close()
        except Exception:
            pass
        self.destroy()


def run(configure=False, action="open", quit_token=None) -> None:
    from stillfind.core.locks import ProcessLock
    token = AppSignals.quit_token()
    if quit_token is not None and token != quit_token:
        return
    lock = ProcessLock.acquire(config.APP_DIR / "window.lock")
    if lock is None:
        AppSignals.signal(action)
        AppSignals.signal_window()
        return
    with lock:
        try:
            if sys.platform == "win32":
                (config.APP_DIR / "window.pid").write_text(str(os.getpid()), encoding="ascii")
            if not security_ui.startup(configure) or token != AppSignals.quit_token():
                return
            with ProcessLock.acquire(config.APP_DIR / "window-tray.lock", blocking=True):
                app = App(token)
                if sys.platform == "darwin":
                    app.createcommand("::tk::mac::ReopenApplication", lambda: app._window_action("open"))
                if action != "open":
                    app.after_idle(lambda: app._window_action(action))
                app.mainloop()
        except (RuntimeError, OSError) as exc:
            messagebox.showerror(tr("Apertura di StillFind"), str(exc))
        finally:
            if sys.platform == "win32":
                (config.APP_DIR / "window.pid").unlink(missing_ok=True)
            ArchiveProtection.forget()
