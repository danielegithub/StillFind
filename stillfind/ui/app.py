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

from stillfind.core import about, config, errors, system, labels as ui_text
from stillfind.indexing.relations import RelationGraph
from stillfind.runtime import agent
from stillfind.runtime.hotkey import Registration
from stillfind.runtime.signals import AppSignals
from stillfind.search.engine import HIT_START, SearchEngine
from stillfind.search.preview import DocumentPreview
from stillfind.search.query import Query, WHERE_ALL, WHERE_NAME
from stillfind.semantic.search import SemanticIndex, SemanticSearch
from stillfind.storage.database import Database
from stillfind.storage.protection import ArchiveProtection
from stillfind.tools.export import CsvExporter
from stillfind.ui import security_ui
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
from stillfind.ui.theme import PAL, col, font, ghost_button, card
from stillfind.ui.views.relations import RelationsPane
from stillfind.ui.views.results import ResultList
from stillfind.ui.views.welcome import WelcomeView
from stillfind.ui.widgets import PreviewAction


ORDERS = {"Più pertinenti": "rank", "Più recenti": "date", "Data del documento": "document_date",
          "Nome (A-Z)": "name"}
RECENT_COUNT = 15  # documenti mostrati quando la ricerca è vuota


class App(ctk.CTk):
    MODE_HITS, MODE_FULL = "Occorrenze", "Testo completo"
    MODE_LINKS = "Collegati"

    def __init__(self, quit_token=None):
        self.cfg = config.Settings.load()
        ctk.set_appearance_mode(self.cfg.get("theme", "system"))
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title("StillFind")
        self.geometry("1240x780")
        self.minsize(940, 600)
        self.configure(fg_color=PAL["bg"])
        self.con = Database.connect()
        self.protection = ArchiveProtection.settings(config.DB_PATH)
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
        self._prog_on = False
        self._prog_mode = "indeterminate"
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
        self.welcome: WelcomeView | None = None
        self._build()
        self.restyle()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._shortcut = Registration(lambda: self.cfg.get("global_shortcut", True),
                                             AppSignals.signal_window, lambda s: self.msgs.put(("shortcut_error", s)))
        self._tray = Registration(lambda: True, lambda action: self.msgs.put(("tray", action)),
                                         lambda _: None, tray=lambda: self._state)
        if self.protection["mode"] == "vault":
            for event in ("<KeyPress>", "<ButtonPress>", "<Motion>", "<MouseWheel>"):
                self.bind_all(event, self._activity, add="+")
        self.after(400, self._session_tick)
        self.after(150, self._poll)
        self.bind("<FocusIn>", self._on_focus)
        self.reschedule_auto_scan()
        if not self.cfg["folders"]:
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
            self.status.configure(text=f"Stato dell’archivio non disponibile · {exc}")
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

    def _show_welcome(self):
        self.welcome = WelcomeView(self, self._finish_welcome)
        self.welcome.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.welcome.lift()

    def _finish_welcome(self, paths: list[str]):
        self.cfg["folders"] = [{"path": p, "recursive": True} for p in paths]
        self.cfg.save()
        if self.welcome is not None:
            self._welcome_vars = self.welcome.choices  # le variabili Tk non devono essere liberate da un altro thread
            self.welcome.destroy()
            self.welcome = None
        self.reschedule_auto_scan()
        self.start_index(force=True)
        self.do_search()
        self.entry.focus_set()

    # ---- costruzione
    def _menu(self, parent, var, values, width, command):
        return ctk.CTkOptionMenu(parent, variable=var, values=values, width=width, height=34, corner_radius=10,
                                 font=font(13), dropdown_font=font(13), fg_color=PAL["card"],
                                 button_color=PAL["card"], button_hover_color=PAL["ghost_hover"],
                                 text_color=PAL["text"], command=command)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # --- in alto: nome, spiegazione e due soli pulsanti
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 0))
        head.grid_columnconfigure(1, weight=1)
        names = ctk.CTkFrame(head, fg_color="transparent")
        names.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(names, text="StillFind", font=font(24, True), text_color=PAL["text"]).pack(anchor="w")
        ctk.CTkLabel(names, text="Cerca dentro i tuoi documenti", font=font(13), text_color=PAL["muted"]).pack(anchor="w")
        ctk.CTkLabel(head, text="🔒  Funziona senza internet: nulla esce dal computer", font=font(12),
                     text_color=PAL["muted"]).grid(row=0, column=1, sticky="w", padx=22, pady=(14, 0))
        self.btn_more = ghost_button(head, "⋯", self._more_menu, width=44)
        self.btn_more.grid(row=0, column=2, padx=(0, 8))
        ghost_button(head, "⚙  Impostazioni", lambda: SettingsDialog(self)).grid(row=0, column=3)

        # --- ricerca
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=22, pady=(14, 0))
        bar.grid_columnconfigure(0, weight=1)
        self.qvar = tk.StringVar()
        self.qvar.trace_add("write", lambda *_: self._schedule())
        self.entry = ctk.CTkEntry(bar, textvariable=self.qvar, height=50, corner_radius=14, font=font(17),
                                  placeholder_text="Cosa cerchi?  Per esempio: contratto affitto, fattura gennaio…",
                                  fg_color=PAL["card"], border_color=PAL["line"], border_width=1,
                                  text_color=PAL["text"])
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Return>", self._submit_query)
        self.entry.bind("<Escape>", lambda e: self.qvar.set(""))
        self.entry.bind("<Down>", lambda e: self._focus_list())
        help_btn = ghost_button(bar, "?", self.show_help, width=50)
        help_btn.configure(height=50, corner_radius=14, font=font(16, True))
        help_btn.grid(row=0, column=1, padx=(10, 0))

        # --- filtri a menu (al posto della sintassi da ricordare a memoria)
        flt = ctk.CTkFrame(self, fg_color="transparent")
        flt.grid(row=2, column=0, sticky="ew", padx=22, pady=(10, 0))
        ctk.CTkLabel(flt, text="Mostra:", font=font(13), text_color=PAL["muted"]).pack(side="left", padx=(2, 8))
        self.type_var = tk.StringVar(value=ui_text.ALL_TYPES)
        self.type_menu = self._menu(flt, self.type_var, [ui_text.ALL_TYPES], 170, lambda _: self.do_search())
        self.type_menu.pack(side="left")
        self.date_var = tk.StringVar(value=ui_text.ANY_DATE)
        self.date_menu = self._menu(flt, self.date_var, list(ui_text.DATE_CHOICES) + [ui_text.CUSTOM_DATE], 170,
                                    self._on_date_choice)
        self.date_menu.pack(side="left", padx=(8, 0))
        self.folder_var = tk.StringVar(value=ui_text.ALL_FOLDERS)
        self.folder_menu = self._menu(flt, self.folder_var, [ui_text.ALL_FOLDERS], 210, self._on_folder_choice)
        self.folder_menu.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(flt, text="Cerca in:", font=font(13), text_color=PAL["muted"]).pack(side="left", padx=(16, 8))
        self.where_var = tk.StringVar(value=ui_text.WHERE_CHOICES[0][0])
        self._menu(flt, self.where_var, [label for label, _ in ui_text.WHERE_CHOICES], 170,
                   lambda _: self.do_search()).pack(side="left")

        # --- risultati (a sinistra) e anteprima (a destra, alta quanto la finestra)
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=3, column=0, sticky="nsew", padx=22, pady=12)
        content.grid_columnconfigure(0, weight=3, minsize=420, uniform="c")
        content.grid_columnconfigure(1, weight=2, minsize=340, uniform="c")
        content.grid_rowconfigure(0, weight=1)

        res = card(content)
        res.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        res.grid_columnconfigure(0, weight=1)
        res.grid_rowconfigure(1, weight=1)
        rh = ctk.CTkFrame(res, fg_color="transparent")
        rh.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 0))
        rh.grid_columnconfigure(0, weight=1)
        self.res_head = ctk.CTkLabel(rh, text="", font=font(14, True), text_color=PAL["text"], anchor="w")
        self.res_head.grid(row=0, column=0, sticky="w")
        self.order_var = tk.StringVar(value="Più pertinenti")
        ctk.CTkLabel(rh, text="Ordina:", font=font(12), text_color=PAL["muted"]).grid(row=0, column=1, padx=(0, 6))
        om = self._menu(rh, self.order_var, list(ORDERS), 150, lambda _: self.do_search())
        om.configure(height=28, corner_radius=8, font=font(12), dropdown_font=font(12))
        om.grid(row=0, column=2)
        self.results = ResultList(res, self._on_select, self.open_selected, self._popup, self.copy_path)
        self.results.grid(row=1, column=0, sticky="nsew", padx=(4, 0), pady=(4, 8))
        self.results.on_more, self.results.on_occurrence = self._toggle_occurrences, self._open_occurrence
        self.empty = ctk.CTkLabel(res, text="", font=font(15), text_color=PAL["muted"], fg_color=PAL["card"],
                                  justify="center", wraplength=380)
        self.empty.bind("<Button-1>", self._apply_hint)
        self.empty.place(relx=0.5, rely=0.55, anchor="center")

        pv = card(content)
        pv.grid(row=0, column=1, sticky="nsew")
        pv.grid_columnconfigure(0, weight=1)
        pv.grid_rowconfigure(3, weight=1)
        ph = ctk.CTkFrame(pv, fg_color="transparent")
        ph.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        ph.grid_columnconfigure(0, weight=1)
        self.pv_title = ctk.CTkLabel(ph, text="Anteprima", anchor="w", font=font(15, True), text_color=PAL["text"],
                                     wraplength=300, justify="left")
        self.pv_title.grid(row=0, column=0, sticky="ew")
        self.pv_path = ctk.CTkLabel(ph, text="Seleziona un risultato per leggerne il contenuto", anchor="w",
                                    font=font(12), text_color=PAL["muted"], wraplength=300, justify="left")
        self.pv_path.grid(row=1, column=0, sticky="ew")
        self.btn_back = ghost_button(ph, "‹ Indietro", self._back)
        self.btn_back.grid(row=0, column=1)
        self.btn_back.grid_remove()
        pb = ctk.CTkFrame(pv, fg_color="transparent")
        pb.grid(row=1, column=0, sticky="ew", padx=16, pady=(10, 0))
        for icon, label, command in (
            ("open", "Apri il file", self.open_selected),
            ("folder", "Mostra nella cartella", self.reveal_selected),
            ("copy", "Copia testo", self.copy_text),
            ("redact", "Oscura il testo in una copia…", lambda: RedactDialog(self)),
        ):
            PreviewAction(pb, icon, label, command).pack(side="left", padx=(0, 6))
        # --- vista del testo: brani attorno alle parole oppure documento intero, con ‹ › tra le occorrenze
        nav = ctk.CTkFrame(pv, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 0))
        nav.grid_columnconfigure(1, weight=1)
        self.pv_mode = ctk.CTkSegmentedButton(nav, values=[self.MODE_HITS, self.MODE_FULL, self.MODE_LINKS], command=self._on_mode,
                                              font=font(12), height=28, selected_color=PAL["accent"],
                                              selected_hover_color=PAL["accent_hover"])
        self.pv_mode.set(self.MODE_HITS)
        self.pv_mode.grid(row=0, column=0, sticky="w")
        self.hit_count = ctk.CTkLabel(nav, text="", font=font(12), text_color=PAL["muted"], anchor="e")
        self.hit_count.grid(row=0, column=1, sticky="e", padx=(8, 6))
        self.btn_hit_prev = ctk.CTkButton(nav, text="‹", width=32, height=28, corner_radius=8, fg_color="transparent",
                                          hover_color=PAL["ghost_hover"], border_width=1, border_color=PAL["line"],
                                          text_color=PAL["text"], font=font(15, True), command=lambda: self.step_hit(-1))
        self.btn_hit_prev.grid(row=0, column=2)
        self.btn_hit_next = ctk.CTkButton(nav, text="›", width=32, height=28, corner_radius=8, fg_color="transparent",
                                          hover_color=PAL["ghost_hover"], border_width=1, border_color=PAL["line"],
                                          text_color=PAL["text"], font=font(15, True), command=lambda: self.step_hit(1))
        self.btn_hit_next.grid(row=0, column=3, padx=(4, 0))
        self.prev = ctk.CTkTextbox(pv, wrap="word", corner_radius=10, fg_color=PAL["field"], font=font(13),
                                   text_color=PAL["text"], border_width=0)
        self.prev.grid(row=3, column=0, sticky="nsew", padx=12, pady=12)
        self.prev.configure(state="disabled")
        self.links = RelationsPane(pv, self)
        self.links.grid(row=3, column=0, sticky="nsew", padx=10, pady=8)
        self.links.grid_remove()

        # --- barra di stato
        st = ctk.CTkFrame(self, fg_color="transparent")
        st.grid(row=4, column=0, sticky="ew", padx=22, pady=(0, 12))
        st.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(st, text="", anchor="w", font=font(12), text_color=PAL["muted"])
        self.status.grid(row=0, column=0, sticky="ew")
        self.btn_due = ghost_button(st, "Scadenze", lambda: DocumentList(self))
        self.btn_diag = ctk.CTkButton(st, text="", height=24, width=0, corner_radius=8,
                                      fg_color="transparent", hover_color=PAL["ghost_hover"],
                                      text_color=PAL["muted"], font=font(12),
                                      command=lambda: DiagnosticsDialog(self))
        self.progress = ctk.CTkProgressBar(st, mode="indeterminate", width=160, height=6,
                                           progress_color=PAL["accent"])
        self.btn_stop = ctk.CTkButton(st, text="■ Ferma", height=24, width=68, corner_radius=8,
                                      fg_color="transparent", hover_color=PAL["ghost_hover"],
                                      border_width=1, border_color=PAL["line"],
                                      text_color=PAL["text"], font=font(11),
                                      command=self.stop_index)

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Apri", command=self.open_selected)
        self.menu.add_command(label="Mostra nella cartella", command=self.reveal_selected)
        self.menu.add_command(label="Copia percorso", command=self.copy_path)
        self.menu.add_command(label="Confronta versioni", command=self.compare_selected)
        self.menu.add_command(label="Oscura copia…", command=lambda: RedactDialog(self))
        self.menu.add_separator()
        self.menu.add_command(label="Dimentica questo file…", command=self._forget_result)
        self.menu.add_command(label="Dimentica la cartella che lo contiene…",
                              command=lambda: self._forget_result(folder=True))
        self.more = tk.Menu(self, tearoff=0)
        self.more.add_command(label="Cronologia e ricerche salvate…", command=lambda: LibraryDialog(self))
        self.more.add_command(label="Mappa dei dati personali…", command=lambda: AuditDialog(self))
        if self.protection["mode"] == "vault":
            self.more.add_command(label="Blocca cassaforte", command=lambda: security_ui.restart(self))
        self.more.add_command(label="Salva i risultati in un file CSV…", command=self.export_results)
        self.more.add_command(label="Cerca nuovi documenti adesso", command=self.start_index)
        self.more.add_separator()
        self.more.add_command(label="Dimentica una cartella…", command=self._forget_folder)
        self.more.add_command(label="Stato dell'archivio…", command=lambda: DiagnosticsDialog(self))
        self.more.add_command(label="Documenti duplicati…", command=lambda: DuplicatesDialog(self))
        self.more.add_command(label="Scadenze…", command=lambda: DocumentList(self))
        self.more.add_separator()
        self.more.add_command(label="Come cercare", command=self.show_help)
        self.more.add_command(label="Informazioni su StillFind", command=lambda: AboutDialog(self))
        for seq in ("<Control-k>", "<Command-k>", "<Control-f>", "<Command-f>"):
            self.bind(seq, lambda e: (self.entry.focus_set(), "break")[1])
        for seq in ("<F3>", "<Control-g>", "<Command-g>"):  # occorrenza successiva / precedente nell'anteprima
            self.bind(seq, lambda e: self.step_hit(1))
        for seq in ("<Shift-F3>", "<Control-G>", "<Command-G>"):
            self.bind(seq, lambda e: self.step_hit(-1))
        self.entry.focus_set()
        self._refresh_filters()
        self.do_search()

    def _more_menu(self):
        b = self.btn_more
        self.more.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def show_help(self):
        HelpDialog(self)

    # ---- aspetto (Tk classici che non accettano coppie chiaro/scuro)
    def restyle(self):
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
        return f"{ui_text.thousands(n)} documenti pronti da cercare" if n else "Nessun documento ancora"

    def _refresh_filters(self):
        exts = [r[0] for r in self.con.execute("SELECT DISTINCT ext FROM docs")]
        values = [ui_text.ALL_TYPES] + ui_text.present_categories(exts)
        self.type_menu.configure(values=values)
        if self.type_var.get() not in values:
            self.type_var.set(ui_text.ALL_TYPES)
        err_n = self.con.execute("SELECT COUNT(*) FROM docs WHERE error != ''").fetchone()[0]
        if err_n:
            self.btn_diag.configure(text=f"{err_n} {'file non letto' if err_n == 1 else 'file non letti'}  ·  perché?")
            self.btn_diag.grid(row=0, column=1, padx=(10, 0))
        else:
            self.btn_diag.grid_forget()
        count = RelationGraph(self.con).upcoming_count() if self.cfg.get("deadline_alerts", True) else 0
        if count:
            self.btn_due.configure(text=f"Scadenze entro 30 giorni · {count} doc.")
            self.btn_due.grid(row=0, column=4, padx=6)
        else:
            self.btn_due.grid_remove()
        self._refresh_folders()
        if not self.indexing:
            self.status.configure(text=self._idle_status())

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
        self.folder_menu.configure(values=[ui_text.ALL_FOLDERS] + list(entries))
        current = next((k for k, v in entries.items() if v == self.folder_filter), None)
        if current is None:
            self.folder_filter = None
        self.folder_var.set(current or ui_text.ALL_FOLDERS)

    def _on_folder_choice(self, label: str):
        self.folder_filter = self._folder_map.get(label)
        self.do_search()

    def _where(self) -> str:
        return dict(ui_text.WHERE_CHOICES).get(self.where_var.get(), WHERE_ALL)

    def _date_bounds(self) -> tuple[float | None, float | None]:
        """(dopo, prima) del filtro «Data»: un periodo scelto a mano oppure una delle scelte rapide."""
        if self._date_custom and self.date_var.get() == self._date_custom[2]:
            return self._date_custom[0], self._date_custom[1]
        return ui_text.date_after(self.date_var.get()), None

    def _on_date_choice(self, choice: str):
        if choice != ui_text.CUSTOM_DATE:
            self.do_search()
            return
        prev = self._date_custom
        # il menu mostra già «Scegli le date…»: si rimette la scelta precedente finché non si conferma
        self.date_var.set(prev[2] if prev else ui_text.ANY_DATE)
        DateRangeDialog(self, self._apply_date_range, prev[3] if prev else "", prev[4] if prev else "")

    def _apply_date_range(self, after, before, label, start_text, end_text):
        self._date_custom = (after, before, label, start_text, end_text)
        self.date_var.set(label)  # il menu mostra il periodo scelto, per esempio «12/03/2025 – 20/04/2025»
        self.do_search()

    def _type_exts(self):
        exts = ui_text.category_exts(self.type_var.get())
        return list(exts) or None

    # ---- ricerca
    def _show_empty(self, text: str, hint: str | None = None):
        self._hint = hint
        self.empty.configure(text=text, cursor="hand2" if hint else "")
        self.empty.place(relx=0.5, rely=0.55, anchor="center")
        self.empty.lift()

    def do_search(self, force: bool = False):
        """Avvia la ricerca in un thread, così la finestra non si blocca mai. Mentre si digita, sotto le 3
        lettere non si cerca (sarebbe troppo lento e inutile): si cerca premendo Invio."""
        self._debounce = None
        q = self.qvar.get().strip()
        if force:
            self._forced_q = q
        short = bool(q) and len(re.sub(r"\W", "", q)) < 3 and q != self._forced_q
        run_q = "" if short else q
        exts = self._type_exts()
        after, before = self._date_bounds()
        where = self._where()
        order = ORDERS[self.order_var.get()]
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
            self.res_head.configure(text="Scrivi almeno 3 lettere, oppure premi Invio")
        elif run_q:
            self.res_head.configure(text="Sto cercando…")

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
            head = "1 risultato" if n == 1 else f"{ui_text.thousands(n)} risultati"
            if n >= 500:
                head += " (i primi 500)"
        elif short:
            head = "Scrivi almeno 3 lettere, oppure premi Invio"
        elif filtered:
            head = "1 documento" if n == 1 else f"{ui_text.thousands(n)} documenti"
        else:
            head = "Documenti recenti" if n else ""
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
            text = (f"Nessun risultato per «{q}» con i filtri scelti.\n\nProva a togliere qualche filtro in alto."
                    if filtered else f"Nessun risultato per «{q}»\n\nProva con meno parole o controlla l'ortografia.")
            if hint:
                text = f"Nessun risultato per «{q}»\n\nIntendevi «{hint}»?\n(clicca qui per cercarlo)"
            self._show_empty(text, hint)
        elif filtered:
            self._show_empty("Nessun documento corrisponde ai filtri scelti.")
        elif self.indexing:
            self._show_empty("Sto leggendo i tuoi documenti…\nTra poco potrai cercarli.")
        elif self.con.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 0:
            self._show_empty("Non ho trovato documenti nelle cartelle scelte.\nControlla le cartelle da Impostazioni.")
        else:
            self._show_empty("Scrivi qui sopra cosa cerchi.")

    def _block(self, r: dict, q: str, roots: list[str]) -> list[tuple[str, tuple]]:
        """Un risultato: titolo, cartella e data, poi la frase in cui compare la parola cercata."""
        where = ui_text.friendly_folder(r["folder"], roots)
        stamp = ui_text.relative_date(r["mtime"])
        if self.order_var.get() == "Data del documento":
            stamp = ("Documento del " + "/".join(reversed(r["document_date"].split("-")))
                     if r.get("document_date") else "Modifica del file: " + stamp)
        segs: list[tuple[str, tuple]] = [
            (ui_text.icon(r["ext"]) + "  ", ("icon",)), (r["name"], ("title",)),
            (f"     {where}  ·  {stamp}", ("meta",)), ("\n", ())]
        if r.get("error") and not r["error"].startswith("Lettura parziale"):
            segs.append(("Non sono riuscito a leggere il contenuto: il file si trova solo per nome.\n", ("snip", "warn")))
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
                segs.append(("Trovato nel nome del file\n", ("snip", "warn")))
        segs.append(("\n", ("gap",)))
        return segs

    def _apply_hint(self, _event=None):
        if self._hint:
            self.qvar.set(self._hint)  # la modifica fa partire la ricerca

    def export_results(self):
        """Salva in CSV i risultati dell'ultima ricerca."""
        rows = list(self.results.results)
        if not rows:
            messagebox.showinfo("Salva i risultati", "Non ci sono risultati da salvare.", parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile="risultati.csv",
                                            filetypes=[("CSV (si apre con Excel)", "*.csv")])
        if path:
            try:
                n = CsvExporter(path).write(rows)
                self.status.configure(text=f"{n} risultati salvati in {system.short_path(path)}")
            except OSError as ex:
                messagebox.showerror("Salva i risultati", f"Non riesco a salvare il file: {ex}", parent=self)

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
        self.pv_title.configure(text="Anteprima")
        self.pv_path.configure(text="Seleziona un risultato per leggerne il contenuto")
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
        self.hit_count.configure(text=("1 occorrenza" if n == 1 else f"{ui_text.thousands(n)} occorrenze") if n else "")

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
        self.hit_count.configure(text=f"{self._hit_i + 1} di {ui_text.thousands(len(self._hit_ranges))}")

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
                self._set_preview([("Preparo il testo completo…", "dim")])
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
                parts = [(f"Non riesco a mostrare il testo: {ex}", "err")]
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
        what = "Testo selezionato copiato"
        if not text:
            if not self._pv_content:
                self.status.configure(text="Non c'è testo da copiare: scegli prima un documento.")
                return
            text, what = self._pv_content.replace("\r\n", "\n"), "Testo del documento copiato"
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.configure(text=f"{what} ({ui_text.thousands(len(text))} caratteri)")

    def _on_select(self):
        self._linked_row = None
        self._back_stack.clear()
        self.btn_back.grid_remove()
        self.show_preview()

    def open_indexed(self, doc_id, start=None, end=None):
        row = SearchEngine(self.con).documents_by_id([doc_id]).get(doc_id)
        if row is None:
            self.status.configure(text="Il documento non è più nell’archivio.")
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
        where = ui_text.friendly_folder(r["folder"], self._roots())
        stamp = (f"Documento del {'/'.join(reversed(r['document_date'].split('-')))} ({r['date_source']})" if r.get("document_date")
                 else f"Modifica del file: {ui_text.relative_date(r['mtime'])}")
        self.pv_path.configure(text=f"{where}  ·  {stamp}"
                                    + ("  ·  OCR utilizzato nel documento" if r.get("ocr") else ""))
        if r.get("error") and not r["error"].startswith("Lettura parziale"):
            self._set_preview([(f"Non sono riuscito a leggere questo file: {r['error']}\n", "err"),
                               ("Il file si trova comunque cercando il suo nome.\n", "dim")])
            return
        # subito: l'estratto già calcolato da SQLite; poi, in un thread, tutte le occorrenze
        quick = DocumentPreview.snippet_parts(r.get("snippet", ""))
        if quick and r.get("location"):
            quick.insert(0, (r["location"] + "\n", "title"))
        self._set_preview(quick + [("\nCarico gli altri punti in cui compare…", "dim")] if quick
                          else [("Carico l'anteprima…", "dim")])
        terms = Query.parse(self.qvar.get()).terms()
        doc_id = r["id"]

        def work():
            content = layout = None
            try:
                content, layout = DocumentPreview.read_document(doc_id)
                parts = (DocumentPreview.anchored_parts(content, *anchor, layout=layout) if anchor else
                         DocumentPreview.preview_parts(content, terms, max_n=DocumentPreview.OCCURRENCES_SHOWN, layout=layout,
                                               preferred=r.get("passage_start")))
            except Exception as ex:
                parts = [(f"Non riesco a mostrare l'anteprima: {ex}", "err")]
            self.msgs.put(("preview", (seq, parts, content, terms, layout)))

        threading.Thread(target=work, daemon=True).start()

    # ---- azioni sul file
    def _current(self):
        """Il risultato attivo: con più righe selezionate, l'ultimo cliccato."""
        return self._linked_row or self.results.active_result()

    def _selected_rows(self) -> list[dict]:
        return self.results.selected_results()

    def _popup(self, e):
        n = len(self.results.selected)
        self.menu.entryconfigure(2, label=f"Copia {n} percorsi" if n > 1 else "Copia percorso")
        self.menu.entryconfigure(3, state="normal" if n == 2 else "disabled")
        self.menu.tk_popup(e.x_root, e.y_root)

    def _forget_result(self, folder=False):
        row = self.results.active_result()
        if row:
            path = os.path.dirname(row["path"]) if folder else row["path"]
            self._confirm_forget(path, folder)

    def _forget_folder(self):
        path = ForgetFolderDialog(self, "Dimentica una cartella dall’archivio").result
        if path:
            self.start_index(force=True, target=path, forget="folder")

    def _confirm_forget(self, path, folder):
        subject = "questa cartella e tutte le sottocartelle" if folder else "questo file"
        if messagebox.askyesno("Dimentica dall’archivio",
                               f"Rimuovere dall’indice {subject}?\n\n{path}\n\n"
                               "Gli originali restano sul disco. Potrai includere di nuovo il percorso "
                               "dalle opzioni avanzate delle Impostazioni.", parent=self):
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
                messagebox.showerror("StillFind", f"Non riesco ad aprire il file:\n{ex}")

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
            self.status.configure(text="Percorso copiato negli appunti" if len(rows) == 1
                                  else f"{len(rows)} percorsi copiati negli appunti")

    # ---- lettura dei documenti in background
    def _show_progress(self, on: bool, mode: str = "indeterminate", value: float = 0.0):
        if on:
            if not self._prog_on:
                self._prog_on = True
                self.progress.grid(row=0, column=2, padx=(12, 0))
                self.btn_stop.grid(row=0, column=3, padx=(8, 0))
                self._prog_mode = ""
            if mode != self._prog_mode:
                self.progress.stop()
                self.progress.configure(mode=mode)
                self._prog_mode = mode
                if mode == "indeterminate":
                    self.progress.start()
            if mode == "determinate":
                self.progress.set(max(0.0, min(1.0, value)))
        elif self._prog_on:
            self._prog_on = False
            self.progress.stop()
            self.progress.grid_forget()
            self.btn_stop.grid_forget()

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
        self.status.configure(text="Mi fermo…")

    def rebuild_index(self):
        self._dirs_sig = None
        self.folder_filter = None
        self.start_index(force=True, rebuild=True)

    def start_index(self, silent=False, force=False, retry_errors=False, target=None, force_ocr=False, rebuild=False,
                    forget=None):
        if not self.cfg["folders"] and not target and not rebuild:
            if not silent:
                self.status.configure(text="Scegli le cartelle da Impostazioni.")
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
            self.status.configure(text="Indicizzazione in pausa")
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
            self.status.configure(text="Rimozione dall’archivio…" if forget else "Cerco i documenti nelle cartelle…")
            self._show_progress(True)

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
                launch_error = "Il lavoratore non è partito nel recinto senza rete."
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
            self._show_progress(False)
            self.status.configure(text=launch_error)
            return
        if st == previous and code is None:
            return
        if st["phase"] == "forget":
            self.status.configure(text="Rimozione dall’archivio…")
        elif st.get("forgotten"):
            self._show_progress(False)
            self._clear_preview()
            self._refresh_filters()
            self.do_search()
            self.status.configure(text=f"File rimossi dall’archivio · {st['removed']}" +
                                       (" · Indicizzazione in pausa" if st["manual_pause"] else ""))
        elif st["manual_pause"] and st["phase"] != "error":
            self._show_progress(active)
            self.status.configure(text="Indicizzazione in pausa")
        elif active:
            if st.get("paused"):
                self._show_progress(True)
                self.status.configure(text="In pausa a batteria. Riprendo quando colleghi l’alimentatore.")
            elif st["phase"] == "embed":
                self._show_progress(True, "determinate", st.get("done", 0) / max(1, st.get("total", 0)))
                self.status.configure(text=f"Calcolo del significato · {st.get('done', 0)} di {st.get('total', 0)}")
            elif st["phase"] == "index" and st.get("total"):
                self._show_progress(True, "determinate", st["done"] / st["total"])
                self.status.configure(text=f"Lettura {min(st['done'] + 1, st['total'])} di {st['total']} · "
                                           f"{st.get('current', '')} · puoi già cercare")
            elif not self._silent or st["run"] != self._own_run:
                self._show_progress(True)
                self.status.configure(text=f"Cerco i documenti nelle cartelle… {st.get('scanned', 0)} trovati")
        elif self._worker is None:
            self._show_progress(False)
            self._refresh_filters()
            if st["phase"] == "error":
                self.status.configure(text="Lettura interrotta · " + st["error"])
            elif st.get("stopped"):
                self.status.configure(text="Lettura interrotta")
            elif st["phase"] == "done":
                details = [f"{st[k]} {label}" for k, label in (("added", "nuovi"), ("updated", "modificati"),
                           ("moved", "spostati"), ("removed", "non più presenti"), ("errors", "non letti")) if st.get(k)]
                if st.get("missing"):
                    details.append("Cartelle non trovate · " + ", ".join(st["missing"]))
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
                            self.status.configure(text="Non trovo altre occorrenze in questo documento.")
                elif kind == "preview_full":
                    seq, parts = data
                    if seq == self._preview_seq:
                        self._pv_full_busy = False
                        self._pv_full_parts = parts
                        if self.pv_mode.get() == self.MODE_FULL:
                            self._render_preview()
                elif kind == "shortcut_error":
                    self.status.configure(text=f"Scorciatoia globale non disponibile · {data}")
        except Exception as ex:  # un errore qui non deve fermare per sempre l'aggiornamento
            try:
                self.status.configure(text=f"Errore interno: {ex}")
            except tk.TclError:
                pass
        self.after(80, self._poll)

    def _close(self):
        if self._closing:
            return
        self._closing = True
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
            messagebox.showerror("Apertura di StillFind", str(exc))
        finally:
            if sys.platform == "win32":
                (config.APP_DIR / "window.pid").unlink(missing_ok=True)
            ArchiveProtection.forget()
