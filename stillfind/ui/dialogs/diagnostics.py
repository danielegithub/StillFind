# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Stato dell'archivio: cosa non si è potuto leggere, perché e che cosa fare."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

import customtkinter as ctk

from stillfind.core import config, labels as ui_text, system

from stillfind.core.problems import ReadProblems
from stillfind.core.i18n import mark, tr
from stillfind.ui.theme import PAL, font, ghost_button, primary_button, card
from stillfind.ui.widgets import SquareSelector

if TYPE_CHECKING:
    from stillfind.ui.app import App


class FailureExplainer:
    """Traduce il messaggio tecnico di un errore di lettura in una causa chiara e in un rimedio."""

    # tipo di problema (vedi `ReadProblems.kind`) → (causa, rimedio)
    RULES = {
        "password": (mark("Il PDF è protetto da password."),
                     mark("Togli la password dal PDF con il programma che usi di solito, poi premi «Rileggi questo file».")),
        "limit": (mark("Il file è più grande del limite impostato."),
                  mark("Aumenta la dimensione massima nelle Impostazioni, poi premi «Rileggi questo file».")),
        "pypdf": (mark("Manca il componente che legge i PDF."),
                  mark("Reinstalla StillFind: nell'app distribuita il componente è già incluso.")),
        "tesseract": (mark("L'OCR non è disponibile."),
                      mark("Nell'app distribuita l'OCR è già incluso: reinstalla StillFind, poi premi «Rileggi questo file».")),
        "pdftoppm": (mark("Non riesco a trasformare le pagine del PDF in immagini per l'OCR."),
                     mark("Reinstalla StillFind, poi premi «Rileggi questo file».")),
        "partial": (mark("Il testo è stato letto solo in parte (alcune pagine non si leggono)."),
                    mark("Il resto del documento si trova già. Per riprovare premi «Rileggi questo file».")),
        "p7m": (mark("La firma digitale non contiene un documento leggibile."),
                mark("Apri il file con il programma di firma: potrebbe essere una firma separata dal documento.")),
        "too_large": (mark("Il file contiene un elemento interno troppo grande."),
                      mark("Apri il file e salvalo di nuovo, oppure ignoralo: si trova comunque cercando il suo nome.")),
    }
    DEFAULT = (mark("Il file è danneggiato o in un formato che StillFind non riconosce."),
               mark("Prova ad aprirlo con «Mostra nella cartella». Se non si apre è danneggiato. Se si apre, premi "
                    "«Rileggi questo file». Altrimenti resta cercabile solo per nome."))

    @classmethod
    def explain(cls, error: str) -> tuple[str, str]:
        cause, advice = cls.RULES.get(ReadProblems.kind(error), cls.DEFAULT)
        return tr(cause), tr(advice)


class DiagnosticsDialog(ctk.CTkToplevel):
    """Stato dell'archivio: numeri in chiaro, un elenco per ogni problema e il rimedio accanto."""

    TAB_ERRORS, TAB_PDF = "errors", "pdf"  # identificatori delle due schede

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title(tr("Stato dell'archivio"))
        self.geometry("900x800")
        self.minsize(760, 680)
        self.configure(fg_color=PAL["bg"])
        self.transient(app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.info = info = app.con.diagnostics()
        self.roots = app._roots()
        self.ocr_on = bool(app.cfg.get("ocr"))
        self._paths: dict[str, dict[str, str]] = {self.TAB_ERRORS: {}, self.TAB_PDF: {}}
        self._errors: dict[str, str] = {}
        self._views: dict[str, dict] = {}

        self._summary(info).grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 12))

        problem_counts = [(self.TAB_ERRORS, len(info["error_docs"])), (self.TAB_PDF, len(info["notext_pdfs"]))]
        titles = {self.TAB_ERRORS: tr("Non letti"), self.TAB_PDF: tr("PDF senza testo")}
        self._tab_names = {f"{titles[name]} ({n})": name for name, n in problem_counts}
        body = ctk.CTkFrame(self, fg_color="transparent", border_width=0)
        body.grid(row=1, column=0, sticky="nsew", padx=16)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        self.tabs = SquareSelector(body, values=list(self._tab_names), command=self._show_tab, font=font(13), height=34)
        self.tabs.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._build_errors(body, info)
        self._build_pdfs(body, info)
        first = self.TAB_ERRORS if info["errors"] or not info["notext_pdfs"] else self.TAB_PDF
        label = next(k for k, v in self._tab_names.items() if v == first)
        self.tabs.set(label)
        self._show_tab(label)

        self._advanced().grid(row=2, column=0, sticky="ew", padx=16, pady=16)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(120, self._grab)

    # ---- riepilogo
    def _summary(self, info):
        frame = card(self)
        frame.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="tiles")
        attention = info["errors"] + info["notext"]
        if not info["total"]:
            headline = tr("L'archivio è vuoto: StillFind non ha ancora letto documenti.")
        elif attention:
            headline = tr("{total} documenti nell'archivio, {attention} da controllare.",
                          total=self._n(info["total"]), attention=self._n(attention))
        else:
            headline = tr("Tutto a posto: {total} documenti letti e pronti da cercare.", total=self._n(info["total"]))
        ctk.CTkLabel(frame, text=headline, font=font(16, True), text_color=PAL["text"], anchor="w", justify="left"
                     ).grid(row=0, column=0, columnspan=4, sticky="ew", padx=16, pady=(14, 8))
        tiles = (
            (info["total"], tr("Documenti nell'archivio"), tr("Tutti si possono cercare per nome.")),
            (info["ocr"], tr("Letti con l'OCR"), tr("Scansioni e foto trasformate in testo.")),
            (info["notext"], tr("Senza testo"), tr("Si trovano solo per nome.")),
            (info["errors"], tr("Non letti"), tr("Il contenuto non è stato letto.")),
        )
        for column, (value, title, note) in enumerate(tiles):
            tile = ctk.CTkFrame(frame, corner_radius=0, fg_color=PAL["field"], border_width=1, border_color=PAL["line"])
            tile.grid(row=1, column=column, sticky="nsew", padx=(16 if column == 0 else 4, 16 if column == 3 else 4))
            warn = column >= 2 and value
            ctk.CTkLabel(tile, text=self._n(value), font=font(24, True), anchor="w",
                         text_color=PAL["err"] if warn else PAL["text"]).pack(fill="x", padx=12, pady=(10, 0))
            ctk.CTkLabel(tile, text=title, font=font(12, True), anchor="w", text_color=PAL["text"]).pack(fill="x", padx=12)
            ctk.CTkLabel(tile, text=note, font=font(11), anchor="w", justify="left", wraplength=170,
                         text_color=PAL["muted"]).pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(frame, text=tr("Occupa {size}  ·  {path}", size=system.human_size(info["db_bytes"]), path=config.DB_PATH), font=font(11),
                     text_color=PAL["muted"], anchor="w").grid(row=2, column=0, columnspan=4, sticky="ew",
                                                                padx=16, pady=(10, 12))
        return frame

    @staticmethod
    def _n(value) -> str:
        return ui_text.thousands(value)

    # ---- elenchi
    def _build_errors(self, parent, info):
        rows = []
        for doc in info["error_docs"]:
            cause, _ = FailureExplainer.explain(doc["error"])
            rows.append((doc["path"], (doc["name"], cause, ui_text.friendly_folder(doc["folder"], self.roots))))
            self._errors[doc["path"]] = doc["error"]
        view = self._section(
            parent, self.TAB_ERRORS,
            tr("Questi file non si sono potuti leggere: restano cercabili solo per nome. Seleziona un file per vedere "
               "il motivo e che cosa fare. Se hai già risolto il problema, riprova a leggerli tutti."),
            tr("Riprova a leggerli tutti"), self._retry, bool(rows),
            (("name", tr("File"), 230), ("why", tr("Perché"), 330), ("folder", tr("Cartella"), 200)), rows,
            tr("Nessun file con problemi: tutti i documenti sono stati letti."), ocr_button=False)
        self._views[self.TAB_ERRORS] = view

    def _build_pdfs(self, parent, info):
        rows = [(d["path"], (d["name"], system.human_size(d["size"]), ui_text.friendly_folder(d["folder"], self.roots)))
                for d in info["notext_pdfs"]]
        if self.ocr_on:
            text = tr("Questi PDF sono scansioni (immagini di pagine), quindi non contengono testo da cercare. "
                      "L'OCR è acceso: premi il pulsante e StillFind li legge in background. Puoi continuare a cercare "
                      "intanto.")
            label, command = tr("Leggi ora questi PDF con l'OCR"), self._read_all_with_ocr
        else:
            text = tr("Questi PDF sono scansioni (immagini di pagine), quindi non contengono testo da cercare. "
                      "Per leggerli serve l'OCR, che oggi è spento. È già incluso nell'app: basta accenderlo "
                      "nelle Impostazioni, poi StillFind li legge da solo.")
            label, command = tr("Accendi l'OCR nelle Impostazioni…"), self._enable_ocr
        view = self._section(
            parent, self.TAB_PDF, text, label, command, bool(rows),
            (("name", tr("File"), 330), ("size", tr("Dimensione"), 110), ("folder", tr("Cartella"), 280)), rows,
            tr("Nessun PDF privo di testo."), ocr_button=True)
        self._views[self.TAB_PDF] = view

    def _section(self, parent, name, text, main_label, main_command, has_rows, specs, rows, empty, ocr_button):
        frame = ctk.CTkFrame(parent, fg_color="transparent", border_width=0)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)
        view = {"frame": frame, "tree": None}
        intro = ctk.CTkFrame(frame, corner_radius=0, fg_color=PAL["card"], border_width=1, border_color=PAL["line"])
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        intro.grid_columnconfigure(0, weight=1)
        label = ctk.CTkLabel(intro, text=text if has_rows else empty, font=font(13), text_color=PAL["text"],
                             anchor="w", justify="left", wraplength=800)
        label.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8 if has_rows else 12))
        intro.bind("<Configure>", lambda e: label.configure(wraplength=max(300, e.width - 28)))
        if not has_rows:
            return view
        primary_button(intro, main_label, main_command).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))

        table = ctk.CTkFrame(frame, corner_radius=0, fg_color=PAL["card"], border_width=1, border_color=PAL["line"])
        table.grid(row=2, column=0, sticky="nsew")
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
        cols = [c[0] for c in specs]
        tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="browse", style="Cerca.Treeview", height=5)
        for key, title, width in specs:
            tree.heading(key, text=title, anchor="w")
            tree.column(key, width=width, minwidth=80, anchor="w", stretch=True)
        for path, values in rows:
            self._paths[name][tree.insert("", "end", values=values)] = path
        tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=6)
        scroll = ctk.CTkScrollbar(table, command=tree.yview, corner_radius=0, width=12)
        scroll.grid(row=0, column=1, sticky="ns", padx=(2, 2), pady=2)
        tree.configure(yscrollcommand=scroll.set)
        tree.bind("<<TreeviewSelect>>", lambda e, n=name: self._select(n))
        tree.bind("<Double-1>", lambda e, n=name: self._reveal(n))
        view["tree"] = tree

        detail = ctk.CTkFrame(frame, corner_radius=0, fg_color=PAL["card"], border_width=1, border_color=PAL["line"])
        detail.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        detail.grid_columnconfigure(0, weight=1)
        view["detail"] = ctk.CTkLabel(detail, text="", font=font(12), text_color=PAL["text"], anchor="w",
                                      justify="left", wraplength=800)
        view["detail"].grid(row=0, column=0, columnspan=4, sticky="ew", padx=14, pady=(10, 8))
        detail.bind("<Configure>", lambda e, v=view: v["detail"].configure(wraplength=max(300, e.width - 28)))
        buttons = ctk.CTkFrame(detail, fg_color="transparent", border_width=0)
        buttons.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))
        view["buttons"] = [
            ghost_button(buttons, tr("Mostra nella cartella"), lambda n=name: self._reveal(n)),
            ghost_button(buttons, tr("Rileggi questo file"), lambda n=name: self._one_selected(n)),
        ]
        if ocr_button:
            view["buttons"].append(ghost_button(buttons, tr("Leggi questo file con l'OCR"),
                                                lambda n=name: self._one_selected(n, ocr=True)))
        for i, button in enumerate(view["buttons"]):
            button.grid(row=0, column=i, padx=(0, 8))
        children = tree.get_children()
        if children:
            tree.selection_set(children[0])
            tree.focus(children[0])
        self._select(name)
        return view

    def _show_tab(self, label):
        name = self._tab_names[label]
        for key, view in self._views.items():
            if key == name:
                view["frame"].grid()
            else:
                view["frame"].grid_remove()
        self.update_idletasks()  # CTk disegna i riquadri appena mostrati solo dopo il layout
        for view in self._views.values():
            if view["frame"].winfo_ismapped():
                self._redraw(view["frame"])

    def _redraw(self, widget):
        draw = getattr(widget, "_draw", None)
        if draw:
            draw()
        for child in widget.winfo_children():
            self._redraw(child)

    def _select(self, name):
        view = self._views.get(name) or {}
        tree = view.get("tree")
        if tree is None or "detail" not in view:
            return
        selected = tree.selection()
        path = self._paths[name].get(selected[0]) if selected else None
        for button in view["buttons"]:
            button.configure(state="normal" if path else "disabled")
        if not path:
            view["detail"].configure(text=tr("Seleziona un file nell'elenco per vedere che cosa fare."))
        elif name == self.TAB_ERRORS:
            cause, advice = FailureExplainer.explain(self._errors[path])
            view["detail"].configure(text=tr("{cause}\nCosa fare: {advice}\nDettaglio tecnico: {detail}", cause=cause,
                                             advice=advice, detail=ReadProblems.display(self._errors[path])))
        else:
            how = (tr("Premi «Leggi questo file con l'OCR» per leggerlo subito.")
                   if self.ocr_on else tr("Con l'OCR spento questo file si trova solo per nome."))
            view["detail"].configure(text=tr("Documento scansionato, senza testo da cercare. {how}", how=how))

    # ---- operazioni avanzate
    def _advanced(self):
        bar = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent", border_width=0)
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bar, text=tr("Altre operazioni"), font=font(11, True), text_color=PAL["muted"], anchor="w"
                     ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        group = ctk.CTkFrame(bar, fg_color="transparent", border_width=0)
        group.grid(row=1, column=0, sticky="w")
        ghost_button(group, tr("Rileggi un file a scelta…"), lambda: self._pick()).pack(side="left", padx=(0, 8))
        ghost_button(group, tr("Rileggi un PDF con l'OCR…"), lambda: self._pick(ocr=True)).pack(side="left", padx=(0, 8))
        ghost_button(group, tr("Svuota e rileggi tutto…"), self._rebuild).pack(side="left")
        ghost_button(bar, tr("Chiudi"), self.destroy).grid(row=1, column=2, sticky="e")
        return bar

    # ---- azioni
    def _grab(self):
        try:
            self.lift()
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass

    def _selected_path(self, name):
        tree = self._views[name]["tree"]
        selected = tree.selection() if tree else ()
        return self._paths[name].get(selected[0]) if selected else None

    def _one_selected(self, name, ocr=False):
        path = self._selected_path(name)
        if path:
            self._reread(path, ocr)

    def _reveal(self, name):
        path = self._selected_path(name)
        if path:
            system.reveal_file(path)

    def _pick(self, ocr=False):
        path = filedialog.askopenfilename(parent=self, title=tr("Scegli un documento già presente nell'archivio"))
        if path:
            self._reread(path, ocr)

    def _reread(self, path, ocr=False):
        if not self.app.con.execute("SELECT 1 FROM docs WHERE path=?", (path,)).fetchone():
            messagebox.showinfo(tr("Stato dell'archivio"), tr("Il file scelto non è presente nell'archivio."), parent=self)
            return
        self.destroy()
        self.app.start_index(force=True, target=path, force_ocr=ocr)

    def _retry(self):
        self.destroy()
        self.app.start_index(force=True, retry_errors=True)

    def _read_all_with_ocr(self):
        self.destroy()
        self.app.start_index(force=True)

    def _enable_ocr(self):
        from stillfind.ui.dialogs.settings import SettingsDialog
        app = self.app
        self.destroy()
        app.after(60, lambda: SettingsDialog(app))

    def _rebuild(self):
        if not messagebox.askyesno(
            tr("Svuota e rileggi tutto"),
            tr("Vuoi svuotare l'archivio e rileggere tutti i documenti da capo?\n\n"
               "Serve solo se qualcosa non torna. Può richiedere molto tempo; i file originali non vengono toccati. "
               "Per i PDF scansionati la lettura delle immagini verrà ripetuta."),
            parent=self,
        ):
            return
        self.destroy()
        self.app.rebuild_index()
