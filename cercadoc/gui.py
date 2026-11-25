# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Interfaccia grafica di CercaDoc (customtkinter: stesso Tk, aspetto moderno).

Si avvia da `cercadoc.cli` (`run()`).
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
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import customtkinter as ctk

from . import about, agent, config, db, duplicates, errors, export, models, preview, semantic, system, ui_text
from . import search as searcher
from . import facts, features_ui, hotkey, protection, relations, security_ui
from .relations_ui import CompareDialog, DocumentList, RelationsPane
from .extractors import ocr as ocr_tools

# (chiaro, scuro)
PAL = {
    "bg": ("#f3f4f7", "#14161a"),
    "card": ("#ffffff", "#1d2026"),
    "line": ("#e2e4ea", "#2a2e37"),
    "text": ("#1c2030", "#e9ebf0"),
    "muted": ("#6a7283", "#8d95a5"),
    "accent": ("#4f46e5", "#6366f1"),
    "accent_hover": ("#4338ca", "#4f46e5"),
    "ghost_hover": ("#e8eaf1", "#262a33"),
    "field": ("#f3f4f7", "#14161a"),
    "hit_bg": ("#fde68a", "#6b5200"),
    "hit_fg": ("#1c2030", "#fff3c4"),
    "hit_now": ("#fb923c", "#c2410c"),   # l'occorrenza su cui ci si trova con ‹ ›
    "sel": ("#e0e7ff", "#2b3158"),
    "err": ("#b91c1c", "#f87171"),
}
THEMES = {"Sistema": "system", "Chiaro": "light", "Scuro": "dark"}


def dark() -> bool:
    return ctk.get_appearance_mode() == "Dark"


def col(name: str) -> str:
    """Colore per la modalità attuale (per i widget Tk che non accettano coppie)."""
    return PAL[name][1 if dark() else 0]


def font(size=13, bold=False) -> ctk.CTkFont:
    return ctk.CTkFont(size=size, weight="bold" if bold else "normal")


def ghost_button(master, text, command, width=0):
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34, corner_radius=10,
                         fg_color="transparent", text_color=PAL["text"], hover_color=PAL["ghost_hover"],
                         border_width=1, border_color=PAL["line"], font=font(13))


def primary_button(master, text, command, width=0):
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34, corner_radius=10,
                         fg_color=PAL["accent"], hover_color=PAL["accent_hover"], text_color="#ffffff",
                         font=font(13, True))


def card(master, **kw):
    return ctk.CTkFrame(master, corner_radius=14, fg_color=PAL["card"], border_width=1,
                        border_color=PAL["line"], **kw)


def _action_icon(action):
    from PIL import Image, ImageDraw

    mask = Image.new("L", (64, 64))
    draw = ImageDraw.Draw(mask)
    if action == "open":
        draw.line([(29, 12), (10, 12), (10, 54), (52, 54), (52, 35)], fill=255, width=5)
        draw.line([(29, 35), (53, 11)], fill=255, width=5)
        draw.line([(35, 11), (53, 11), (53, 29)], fill=255, width=5)
    elif action == "folder":
        draw.line([(7, 51), (7, 13), (27, 13), (33, 20), (57, 20), (57, 51), (7, 51)],
                  fill=255, width=5, joint="curve")
        draw.line([(8, 27), (55, 27)], fill=255, width=4)
    elif action == "copy":
        draw.line([(15, 42), (10, 42), (10, 9), (40, 9), (40, 14)], fill=255, width=5)
        draw.rounded_rectangle((21, 19, 54, 55), radius=4, outline=255, width=5)
    elif action == "redact":
        draw.rounded_rectangle((9, 10, 55, 54), radius=5, outline=255, width=5)
        draw.line([(18, 22), (39, 22)], fill=255, width=4)
        draw.rectangle((17, 31, 47, 39), fill=255)
    else:
        raise ValueError(action)

    def tinted(color):
        icon = Image.new("RGBA", mask.size, color)
        icon.putalpha(mask)
        return icon

    light, dark_color = ("white", "white") if action == "open" else PAL["text"]
    return ctk.CTkImage(tinted(light), tinted(dark_color), size=(20, 20))


class PreviewAction(ctk.CTkButton):
    def __init__(self, master, action, label, command):
        primary = action == "open"
        self._tip_text = label
        self._tip = self._tip_job = None

        def activate():
            self._hide_tip()
            command()

        super().__init__(master, text="", image=_action_icon(action), command=activate,
                         width=38, height=36, corner_radius=9, border_width=1, border_spacing=0,
                         fg_color=PAL["accent"] if primary else "transparent",
                         border_color=PAL["accent"] if primary else PAL["line"],
                         hover_color=PAL["accent_hover"] if primary else PAL["ghost_hover"])
        tk.Frame.configure(self, takefocus=True)
        self.bind("<Enter>", self._schedule_tip)
        self.bind("<Leave>", self._hide_tip)
        # CTkButton.bind indirizza gli eventi ai figli grafici. Il focus di Tab arriva alla cornice.
        tk.Misc.bind(self, "<FocusIn>", self._show_tip)
        for event in ("<FocusOut>", "<Escape>", "<Unmap>"):
            tk.Misc.bind(self, event, self._hide_tip)
        for key in ("<Return>", "<space>"):
            tk.Misc.bind(self, key, self._press)

    def _press(self, event):
        self.invoke()
        return "break"

    def _schedule_tip(self, event):
        if self._tip is None and self._tip_job is None:
            self._tip_job = self.after(450, self._show_tip)

    def _show_tip(self, event=None):
        self._hide_tip()
        top = self.winfo_toplevel()
        self._tip = tk.Label(top, text=self._tip_text, bg=col("text"), fg=col("card"),
                             padx=8, pady=4, takefocus=False)
        x = self.winfo_rootx() - top.winfo_rootx()
        y = self.winfo_rooty() - top.winfo_rooty()
        below = y + self.winfo_height() + 4
        h = self._tip.winfo_reqheight()
        self._tip.place(x=max(0, min(x, top.winfo_width() - self._tip.winfo_reqwidth())),
                        y=below if below + h <= top.winfo_height() else max(0, y - h - 4))
        self._tip.lift()

    def _hide_tip(self, event=None):
        if self._tip_job is not None:
            self.after_cancel(self._tip_job)
            self._tip_job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def destroy(self):
        self._hide_tip()
        super().destroy()


# --------------------------------------------------------------------------
class DiagnosticsDialog(ctk.CTkToplevel):
    """Stato dell'archivio: file che non si sono potuti leggere e PDF senza testo."""

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title("Stato dell'archivio")
        self.geometry("760x560")
        self.minsize(600, 440)
        self.configure(fg_color=PAL["bg"])
        self.transient(app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        info = db.diagnostics(app.con)
        self._err_paths: dict[str, str] = {}
        self._pdf_paths: dict[str, str] = {}

        # --- riepilogo
        top = card(self)
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))
        summary = (
            f"Documenti: {info['total']:,}   ·   Letti da scansione: {info['ocr']:,}   ·   "
            f"Senza testo: {info['notext']:,}   ·   Non letti: {info['errors']:,}   ·   "
            f"Spazio occupato: {system.human_size(info['db_bytes'])}"
        ).replace(",", ".")
        ctk.CTkLabel(top, text=summary, font=font(13, True), text_color=PAL["text"], anchor="w"
                     ).pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(top, text=f"Archivio: {config.DB_PATH}", font=font(11), text_color=PAL["muted"], anchor="w"
                     ).pack(fill="x", padx=14, pady=(0, 10))

        # --- schede
        tabs = ctk.CTkTabview(self, fg_color=PAL["card"], segmented_button_selected_color=PAL["accent"],
                              segmented_button_selected_hover_color=PAL["accent_hover"])
        tabs.grid(row=1, column=0, sticky="nsew", padx=14)
        err_tab = tabs.add(f"File non letti ({len(info['error_docs'])})")
        pdf_tab = tabs.add(f"PDF senza testo ({len(info['notext_pdfs'])})")

        self._build_list(
            err_tab, ("name", "error", "folder"),
            (("name", "File", 190), ("error", "Motivo", 270), ("folder", "Cartella", 220)),
            [(d["path"], (d["name"], d["error"], d["folder"])) for d in info["error_docs"]],
            "Tutti i file sono stati letti.", self._err_paths,
        )
        self._build_list(
            pdf_tab, ("name", "size", "ocr", "folder"),
            (("name", "File", 220), ("size", "Dim.", 80), ("ocr", "Scansione", 80), ("folder", "Cartella", 300)),
            [(d["path"], (d["name"], system.human_size(d["size"]), "Sì" if d["ocr"] else "No", d["folder"]))
             for d in info["notext_pdfs"]],
            "Nessun PDF privo di testo.", self._pdf_paths,
        )
        single = ctk.CTkFrame(top, fg_color="transparent")
        single.pack(fill="x", padx=14, pady=(0, 8))
        ghost_button(single, "Rileggi un file…", lambda: self._one()).pack(side="left")
        ghost_button(single, "Esegui OCR su un file…", lambda: self._one(ocr=True)).pack(side="left", padx=6)

        # --- pulsanti
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=14, pady=14)
        bar.grid_columnconfigure(1, weight=1)
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0)
        ghost_button(left, "Rileggi tutto da capo", self._rebuild).pack(side="left")
        ghost_button(left, "Trova duplicati…", lambda: DuplicatesDialog(self)).pack(side="left", padx=(8, 0))
        if info["errors"]:
            primary_button(bar, "Riprova i file non letti", self._retry).grid(row=0, column=2, padx=(0, 8))
        ghost_button(bar, "Chiudi", self.destroy).grid(row=0, column=3)
        self.after(120, self._grab)

    def _grab(self):
        try:
            self.lift()
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass

    def _build_list(self, parent, cols, specs, items, empty_msg, path_map):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        if not items:
            ctk.CTkLabel(parent, text=empty_msg, font=font(13), text_color=PAL["muted"]
                         ).grid(row=0, column=0, pady=24)
            return
        tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="browse", style="Cerca.Treeview")
        for c, label, w in specs:
            tree.heading(c, text=label, anchor="w")
            tree.column(c, width=w, anchor="w", stretch=True)
        for p, vals in items:
            iid = tree.insert("", "end", values=vals)
            path_map[iid] = p
        tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        sb = ctk.CTkScrollbar(parent, command=tree.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=4)
        tree.configure(yscrollcommand=sb.set)
        tree.bind("<Double-1>", lambda e, t=tree, m=path_map: self._reveal(t, m))
        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="w")
        for label, ocr in (("Rileggi selezione", False), ("Esegui OCR sulla selezione", True)):
            ghost_button(actions, label, lambda flag=ocr: self._one(
                path_map.get(tree.focus()), flag, choose=False)).pack(side="left", padx=4)

    def _one(self, path=None, ocr=False, choose=True):
        if choose and not path:
            path = filedialog.askopenfilename(parent=self, title="Scegli un documento già indicizzato")
        if not path:
            return
        if not self.app.con.execute("SELECT 1 FROM docs WHERE path=?", (path,)).fetchone():
            messagebox.showinfo("Diagnostica", "Il file scelto non è presente nell’indice.", parent=self)
            return
        self.destroy()
        self.app.start_index(force=True, target=path, force_ocr=ocr)

    def _reveal(self, tree, path_map):
        sel = tree.selection()
        if sel and sel[0] in path_map:
            system.reveal_file(path_map[sel[0]])

    def _retry(self):
        self.destroy()
        self.app.start_index(force=True, retry_errors=True)

    def _rebuild(self):
        if not messagebox.askyesno(
            "Rileggi tutto da capo",
            "Vuoi svuotare l'archivio e rileggere tutti i documenti da capo?\n"
            "(Per i PDF scansionati la lettura delle immagini verrà ripetuta.)",
            parent=self,
        ):
            return
        self.destroy()
        self.app.rebuild_index()


# --------------------------------------------------------------------------
class AboutDialog(ctk.CTkToplevel):
    """Informazioni su CercaDoc: autore, licenza e assenza di garanzia (GPL-3.0, sezione 5d)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Informazioni su CercaDoc")
        self.geometry("520x420")
        self.resizable(False, False)
        self.configure(fg_color=PAL["bg"])
        self.transient(parent)
        ctk.CTkLabel(self, text="CercaDoc", font=font(24, True), text_color=PAL["text"]
                     ).pack(anchor="w", padx=24, pady=(22, 0))
        ctk.CTkLabel(self, text=f"Versione {about.__version__}", font=font(13), text_color=PAL["muted"]
                     ).pack(anchor="w", padx=24)
        ctk.CTkLabel(self, text=about.NOTICE, font=font(13), text_color=PAL["text"], wraplength=470,
                     justify="left", anchor="w").pack(anchor="w", padx=24, pady=16)
        primary_button(self, "Chiudi", self.destroy).pack(anchor="e", padx=24, side="bottom", pady=18)


class DuplicatesDialog(ctk.CTkToplevel):
    """Documenti con contenuto identico (SHA-256 completo), dai più ingombranti."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Documenti duplicati")
        self.geometry("820x520")
        self.minsize(620, 380)
        self.configure(fg_color=PAL["bg"])
        self.transient(parent)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._stop = threading.Event()
        self._q: queue.Queue = queue.Queue()
        self._paths: dict[str, str] = {}
        self.label = ctk.CTkLabel(self, text="Sto confrontando i file…", font=font(13, True),
                                  text_color=PAL["text"], anchor="w")
        self.label.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        box = card(self)
        box.grid(row=1, column=0, sticky="nsew", padx=14)
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(box, columns=("size",), selectmode="browse", style="Cerca.Treeview")
        self.tree.heading("#0", text="File", anchor="w")
        self.tree.heading("size", text="Dimensione", anchor="e")
        self.tree.column("#0", width=600, stretch=True)
        self.tree.column("size", width=110, anchor="e", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        sb = ctk.CTkScrollbar(box, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=4)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", self._reveal)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=14, pady=14)
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="Doppio clic su un file per mostrarlo nella sua cartella. Nessun file viene cancellato.",
                     font=font(12), text_color=PAL["muted"], anchor="w").grid(row=0, column=0, sticky="w")
        ghost_button(bar, "Chiudi", self._close).grid(row=0, column=1)
        self.protocol("WM_DELETE_WINDOW", self._close)
        threading.Thread(target=self._work, daemon=True).start()
        self.after(150, self._poll)

    def _work(self):
        con = db.connect()  # connessione propria: quella della finestra non si può usare da un altro thread
        try:
            res = duplicates.find_duplicates(
                con, self._stop, lambda i, n: self._q.put(("progress", (i, n))))
            self._q.put(("done", res))
        except errors.Stopped:
            pass
        except Exception as ex:
            self._q.put(("error", str(ex)))
        finally:
            con.close()

    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "progress":
                    self.label.configure(text=f"Sto confrontando i file… ({data[0] + 1} di {data[1]})")
                elif kind == "error":
                    self.label.configure(text=f"Errore: {data}")
                    return
                else:
                    self._show(data)
                    return
        except queue.Empty:
            pass
        if self.winfo_exists() and not self._stop.is_set():
            self.after(150, self._poll)

    def _show(self, groups):
        if not groups:
            self.label.configure(text="Non ci sono documenti duplicati.")
            return
        wasted = sum(g["wasted"] for g in groups)
        self.label.configure(text=f"{len(groups)} gruppi di copie identiche · spazio che puoi liberare: {system.human_size(wasted)}")
        for g in groups:
            parent = self.tree.insert("", "end", open=True, text=f"{len(g['paths'])} copie identiche",
                                      values=(system.human_size(g["size"]),))
            for path in g["paths"]:
                iid = self.tree.insert(parent, "end", text=path, values=(system.human_size(g["size"]),))
                self._paths[iid] = path

    def _reveal(self, _event=None):
        sel = self.tree.selection()
        if sel and sel[0] in self._paths:
            system.reveal_file(self._paths[sel[0]])

    def _close(self):
        self._stop.set()
        self.destroy()


class DateRangeDialog(ctk.CTkToplevel):
    """Scelta di un intervallo di date a mano: «dal» e «al», anche una sola. `on_apply(dopo, prima, etichetta)`."""

    def __init__(self, parent, on_apply, start: str = "", end: str = ""):
        super().__init__(parent)
        self.title("Scegli le date")
        self.geometry("460x330")
        self.resizable(False, False)
        self.configure(fg_color=PAL["bg"])
        self.transient(parent)
        self._on_apply = on_apply
        ctk.CTkLabel(self, text="Documenti modificati in questo periodo", font=font(17, True),
                     text_color=PAL["text"]).pack(anchor="w", padx=22, pady=(18, 2))
        ctk.CTkLabel(self, text="Scrivi le date come 31/12/2025. Basta anche il mese (12/2025) o l'anno (2025): "
                                "vale tutto il periodo. Puoi lasciare vuota una delle due.",
                     font=font(12), text_color=PAL["muted"], wraplength=410, justify="left", anchor="w"
                     ).pack(anchor="w", padx=22, pady=(0, 12))
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=22)
        form.grid_columnconfigure(1, weight=1)
        self.entries = []
        for row, (label, value) in enumerate((("Dal", start), ("Al", end))):
            ctk.CTkLabel(form, text=label, font=font(13), text_color=PAL["text"], width=40, anchor="w"
                         ).grid(row=row, column=0, pady=6)
            e = ctk.CTkEntry(form, height=36, corner_radius=10, font=font(14), placeholder_text="gg/mm/aaaa",
                             fg_color=PAL["card"], border_color=PAL["line"], text_color=PAL["text"])
            e.grid(row=row, column=1, sticky="ew", pady=6)
            if value:
                e.insert(0, value)
            e.bind("<Return>", lambda _e: self._apply())
            self.entries.append(e)
        self.error = ctk.CTkLabel(self, text="", font=font(12), text_color=PAL["err"], wraplength=410,
                                  justify="left", anchor="w")
        self.error.pack(anchor="w", padx=22, pady=(6, 0))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=22, pady=16)
        primary_button(bar, "Applica", self._apply).pack(side="right")
        ghost_button(bar, "Annulla", self.destroy).pack(side="right", padx=8)
        self.after(150, self._focus)

    def _focus(self):
        try:
            self.grab_set()
            self.entries[0].focus_set()
        except tk.TclError:
            pass

    def _apply(self):
        start, end = self.entries[0].get().strip(), self.entries[1].get().strip()  # prima di chiudere: dopo i campi non esistono più
        try:
            after, before, label = ui_text.parse_date_range(start, end)
        except ValueError as ex:
            self.error.configure(text=str(ex))
            return
        self.destroy()
        self._on_apply(after, before, label, start, end)


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


class WelcomeView(ctk.CTkFrame):
    """Primo avvio: una frase che spiega cosa fa CercaDoc e un solo passo, scegliere le cartelle."""

    SUGGESTED = (("Documenti", ("Documents", "Documenti"), True),
                 ("Scrivania", ("Desktop", "Scrivania"), True),
                 ("Download", ("Downloads", "Download"), False))

    def __init__(self, app, on_start):
        super().__init__(app, fg_color=PAL["bg"], corner_radius=0)
        self.on_start = on_start
        self.choices: list[tuple[str, ctk.BooleanVar]] = []
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(box, text="🔍", font=font(46)).pack()
        ctk.CTkLabel(box, text="CercaDoc", font=font(32, True), text_color=PAL["text"]).pack(pady=(2, 0))
        ctk.CTkLabel(box, text="Trova le parole dentro i tuoi documenti:\\nPDF, Word, Excel, email e molto altro.",
                     font=font(15), text_color=PAL["muted"], justify="center").pack(pady=(6, 4))
        ctk.CTkLabel(box, text="🔒  Funziona senza internet: nulla esce dal tuo computer.", font=font(13, True),
                     text_color=PAL["accent"]).pack(pady=(0, 24))
        ctk.CTkLabel(box, text="Dove sono i tuoi documenti?", font=font(15, True), text_color=PAL["text"],
                     anchor="w").pack(fill="x")
        self.list = card(box)
        self.list.pack(fill="x", pady=(8, 8))
        self.list.grid_columnconfigure(0, weight=1)
        for label, names, checked in self.SUGGESTED:
            path = next((str(Path.home() / n) for n in names if (Path.home() / n).is_dir()), None)
            if path:
                self._add(label, path, checked)
        self._placeholder = None
        if not self.choices:
            self._placeholder = ctk.CTkLabel(self.list, text="Scegli la cartella che contiene i tuoi documenti.",
                                             font=font(13), text_color=PAL["muted"])
            self._placeholder.grid(row=0, column=0, padx=16, pady=14)
        ghost_button(box, "＋  Scegli un'altra cartella…", self._choose).pack(anchor="w")
        ghost_button(box, "Protezione dell’archivio…", lambda: security_ui.restart(app, True)).pack(anchor="w", pady=5)
        btn = primary_button(box, "Inizia", self._start)
        btn.configure(height=46, corner_radius=14, font=font(16, True))
        btn.pack(fill="x", pady=(24, 8))
        ctk.CTkLabel(box, text="CercaDoc legge anche le sottocartelle. Potrai cambiare le cartelle quando vuoi\\n"
                               "dalle Impostazioni. All'inizio serve qualche minuto, ma puoi già cercare.",
                     font=font(12), text_color=PAL["muted"], justify="center").pack()

    def _add(self, label: str, path: str, checked: bool = True):
        if any(p == path for p, _ in self.choices):
            return
        var = ctk.BooleanVar(value=checked)
        self.choices.append((path, var))
        row = len(self.choices) - 1
        if getattr(self, "_placeholder", None) is not None:
            self._placeholder.destroy()
            self._placeholder = None
        ctk.CTkCheckBox(self.list, text=label, variable=var, checkbox_width=22, checkbox_height=22, corner_radius=6,
                        fg_color=PAL["accent"], hover_color=PAL["accent_hover"], font=font(14),
                        text_color=PAL["text"]).grid(row=row, column=0, sticky="w", padx=16, pady=(10, 2))
        ctk.CTkLabel(self.list, text=path, font=font(11),
                     text_color=PAL["muted"], anchor="w").grid(row=row, column=1, sticky="w", padx=(0, 16), pady=(10, 2))

    def _choose(self):
        d = filedialog.askdirectory(parent=self.winfo_toplevel(), title="Scegli una cartella")
        if d:
            self._add(os.path.basename(d.rstrip("/\\\\")) or d, d, True)

    def _start(self):
        paths = [p for p, v in self.choices if v.get()]
        if not paths:
            messagebox.showinfo("CercaDoc", "Spunta almeno una cartella, oppure scegline una.",
                                parent=self.winfo_toplevel())
            return
        self.on_start(paths)


class ResultList(ctk.CTkFrame):
    """Elenco dei risultati: titolo, cartella, data e la frase in cui compare la parola cercata.

    È un solo widget Text (veloce anche con 500 righe). Selezione con clic, Maiusc per un intervallo,
    ⌘/Ctrl per aggiungere; frecce, Invio, doppio clic."""

    def __init__(self, master, on_change, on_open, on_menu, on_copy):
        super().__init__(master, fg_color="transparent")
        self.on_change, self.on_open, self.on_menu, self.on_copy = on_change, on_open, on_menu, on_copy
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.text = tk.Text(self, wrap="word", cursor="arrow", borderwidth=0, highlightthickness=0, padx=14, pady=4,
                            exportselection=False, takefocus=True, undo=False, insertwidth=0, spacing1=0, spacing3=0)
        self.text.grid(row=0, column=0, sticky="nsew")
        sb = ctk.CTkScrollbar(self, command=self.text.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=6)
        self.text.configure(yscrollcommand=sb.set)
        self.results: list[dict] = []
        self.selected: list[int] = []
        self.active: int | None = None
        self._anchor: int | None = None
        # «Altre occorrenze»: per ogni risultato False = chiuso, None = in caricamento, (voci, totale) = aperto
        self.open_items: dict[int, object] = {}
        self.on_more = self.on_occurrence = None
        for name in ("pick", "icon", "title", "meta", "snip", "warn", "gap", "more", "xpage", "xsnip", "xmuted", "hit"):
            self.text.tag_configure(name)  # l'ordine è la priorità
        self.restyle()
        self.text.configure(state="disabled")
        t = self.text
        t.bind("<Button-1>", lambda e: self._click(e))
        t.bind("<Shift-Button-1>", lambda e: self._click(e, extend=True))
        t.bind("<Command-Button-1>" if sys.platform == "darwin" else "<Control-Button-1>",
               lambda e: self._click(e, toggle=True))
        t.bind("<Double-Button-1>", self._double)
        for seq in ("<B1-Motion>", "<Triple-Button-1>", "<ButtonRelease-1>", "<Shift-B1-Motion>"):
            t.bind(seq, lambda e: "break")
        for seq in ("<Button-2>", "<Button-3>") + (("<Control-Button-1>",) if sys.platform == "darwin" else ()):
            t.bind(seq, self._context)
        t.bind("<Up>", lambda e: self._move(-1))
        t.bind("<Down>", lambda e: self._move(1))
        t.bind("<Shift-Up>", lambda e: self._move(-1, True))
        t.bind("<Shift-Down>", lambda e: self._move(1, True))
        t.bind("<Prior>", lambda e: self._move(-5))
        t.bind("<Next>", lambda e: self._move(5))
        t.bind("<Home>", lambda e: self._go(0))
        t.bind("<End>", lambda e: self._go(len(self.results) - 1))
        t.bind("<Return>", lambda e: (self.on_open(), "break")[1])
        for seq in ("<Command-a>", "<Control-a>"):
            t.bind(seq, lambda e: (self.select_all(), "break")[1])
        for seq in ("<Command-c>", "<Control-c>"):
            t.bind(seq, lambda e: (self.on_copy(), "break")[1])

    def restyle(self):
        t = self.text
        t.configure(background=col("card"), foreground=col("text"), selectbackground=col("card"),
                    selectforeground=col("text"), inactiveselectbackground=col("card"))
        t.tag_configure("pick", background=col("sel"))
        t.tag_configure("icon", font=("", 15))
        t.tag_configure("title", font=("", 14, "bold"), foreground=col("text"))
        t.tag_configure("meta", font=("", 12), foreground=col("muted"))
        t.tag_configure("snip", font=("", 13), foreground=col("text"), lmargin1=34, lmargin2=34)
        t.tag_configure("warn", font=("", 12, "italic"), foreground=col("muted"))
        t.tag_configure("gap", font=("", 7))
        t.tag_configure("more", font=("", 12, "bold"), foreground=col("accent"), lmargin1=34, lmargin2=34)
        t.tag_configure("xpage", font=("", 12, "bold"), foreground=col("muted"), lmargin1=48, lmargin2=48)
        t.tag_configure("xsnip", font=("", 12), foreground=col("text"), lmargin1=48, lmargin2=48)
        t.tag_configure("xmuted", font=("", 12, "italic"), foreground=col("muted"), lmargin1=34, lmargin2=34)
        t.tag_configure("hit", background=col("hit_bg"), foreground=col("hit_fg"))
        t.tag_raise("hit")
        for name in ("more", "occ"):  # mano sopra le voci cliccabili
            t.tag_bind(name, "<Enter>", lambda e: t.configure(cursor="hand2"))
            t.tag_bind(name, "<Leave>", lambda e: t.configure(cursor="arrow"))

    # ---- contenuto
    def show(self, results: list[dict], blocks: list[list[tuple[str, tuple]]]):
        self.results, self.selected, self.active, self._anchor = results, [], None, None
        self.open_items = {}
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for name in [m for m in t.mark_names() if m.startswith("ins")]:
            t.mark_unset(name)
        for i, segs in enumerate(blocks):
            start = t.index("end-1c")
            for text, tags in segs[:-1]:
                t.insert("end", text, tags)
            t.mark_set(f"ins{i}", "end-1c")      # qui, prima dello spazio finale, si inseriscono le «altre occorrenze»
            t.mark_gravity(f"ins{i}", "left")
            t.insert("end", *segs[-1])
            t.tag_add(f"res{i}", start, "end-1c")
        t.configure(state="disabled")
        t.yview_moveto(0)

    def active_result(self) -> dict | None:
        return self.results[self.active] if self.active is not None and self.active < len(self.results) else None

    def selected_results(self) -> list[dict]:
        return [self.results[i] for i in sorted(self.selected) if i < len(self.results)]

    # ---- selezione
    def _index_at(self, e) -> int | None:
        for tag in self.text.tag_names(self.text.index(f"@{e.x},{e.y}")):
            if tag.startswith("res") and tag[3:].isdigit():
                return int(tag[3:])
        return None

    def _extra_segs(self, i: int) -> list[tuple[str, tuple]]:
        """Righe da mostrare sotto il risultato `i` quando è quello attivo: il comando per vedere le altre
        occorrenze, oppure l'elenco con pagina e contesto."""
        if not self.results[i].get("expandable"):
            return []
        base = (f"res{i}", "xline")
        state = self.open_items.get(i, False)
        if state is False:
            return [("▸ Altre occorrenze\n", base + ("more",))]
        if state is None:
            return [("Cerco le occorrenze…\n", base + ("xmuted",))]
        items, total = state
        segs = [(f"▾ Nascondi le occorrenze ({ui_text.thousands(total)})\n", base + ("more",))]
        for k, it in enumerate(items):
            tags = base + ("occ", f"occ{k}")
            segs.append((it["location"] + " — " if it.get("location") else "", tags + ("xpage",)))
            segs += [(text, tags + ("xsnip",) + (("hit",) if tag == "hit" else ())) for text, tag in it["parts"]]
            segs.append(("\n", tags + ("xsnip",)))
        more = total - sum(it["count"] for it in items)
        if more > 0:
            segs.append((f"… e altre {ui_text.thousands(more)}: nell'anteprima si vedono tutte\n", base + ("xmuted",)))
        return segs

    def _refresh_extra(self):
        t = self.text
        t.configure(state="normal")
        r = t.tag_ranges("xline")
        for k in range(len(r) - 2, -1, -2):
            t.delete(r[k], r[k + 1])
        i = self.active
        if i is not None and i < len(self.results) and f"ins{i}" in t.mark_names():
            pos = t.index(f"ins{i}")
            for text, tags in self._extra_segs(i):
                t.insert(pos, text, tags)
                pos = t.index(f"{pos}+{len(text)}c")
        t.configure(state="disabled")

    def set_open(self, i: int, state) -> None:
        """Cambia lo stato delle «altre occorrenze» del risultato `i` e ridisegna."""
        self.open_items[i] = state
        self._paint()

    def _paint(self, see: bool = False):
        t = self.text
        self._refresh_extra()
        t.tag_remove("pick", "1.0", "end")
        for i in self.selected:
            r = t.tag_ranges(f"res{i}")
            if r:
                t.tag_add("pick", r[0], r[1])
        if see and self.active is not None:
            t.see(f"res{self.active}.first")

    def select(self, i: int | None, extend=False, toggle=False, see=False):
        if i is None or not (0 <= i < len(self.results)):
            return
        if toggle:
            if i in self.selected:
                self.selected.remove(i)
            else:
                self.selected.append(i)
            self._anchor = i
            self.active = i if i in self.selected else (self.selected[-1] if self.selected else None)
        elif extend and self._anchor is not None:
            a, b = sorted((self._anchor, i))
            self.selected, self.active = list(range(a, b + 1)), i
        else:
            self.selected, self._anchor, self.active = [i], i, i
        self._paint(see)
        self.on_change()

    def select_all(self):
        if self.results:
            self.selected = list(range(len(self.results)))
            self.active = self.active if self.active is not None else 0
            self._paint()
            self.on_change()

    def _click(self, e, extend=False, toggle=False):
        self.text.focus_set()
        i = self._index_at(e)
        if i is not None and not extend and not toggle:
            tags = self.text.tag_names(f"@{e.x},{e.y}")
            if "more" in tags:  # «▸ Altre occorrenze» / «▾ Nascondi»
                self.select(i)
                if self.on_more:
                    self.on_more(i)
                return "break"
            occ = next((int(x[3:]) for x in tags if x.startswith("occ") and x[3:].isdigit()), None)
            state = self.open_items.get(i)
            if occ is not None and isinstance(state, tuple) and occ < len(state[0]):
                self.select(i)
                if self.on_occurrence:
                    self.on_occurrence(i, state[0][occ])
                return "break"
        self.select(i, extend, toggle)
        return "break"

    def _double(self, e):
        i = self._index_at(e)
        if i is not None:
            self.select(i)
            self.on_open()
        return "break"

    def _context(self, e):
        i = self._index_at(e)
        if i is not None:
            if i not in self.selected:
                self.select(i)
            self.on_menu(e)
        return "break"

    def _move(self, delta: int, extend: bool = False):
        if self.results:
            cur = self.active if self.active is not None else (-1 if delta > 0 else len(self.results))
            self.select(max(0, min(len(self.results) - 1, cur + delta)), extend=extend, see=True)
        return "break"

    def _go(self, i: int):
        self.select(max(0, i), see=True)
        return "break"

    def focus_list(self):
        self.text.focus_set()
        if self.results and self.active is None:
            self.select(0, see=True)


class ForgetFolderDialog(simpledialog.Dialog):
    def body(self, master):
        folders = [row[0] for row in self.parent.con.execute(
            "SELECT path FROM dirs UNION SELECT folder FROM docs ORDER BY 1 COLLATE NOCASE")]
        ttk.Label(master, text="Cartella da dimenticare").pack(anchor="w")
        self.choice = ttk.Combobox(master, values=folders, state="readonly", width=64)
        self.choice.pack(fill="x", pady=6)
        if folders:
            self.choice.set(self.parent.folder_filter if self.parent.folder_filter in folders else folders[0])
        ttk.Label(master, text="La rimozione include le sottocartelle. Gli originali restano sul disco.\n"
                              "Puoi includere di nuovo la cartella dalle Impostazioni.").pack(anchor="w")
        return self.choice

    def buttonbox(self):
        bar = ttk.Frame(self)
        bar.pack(anchor="e", padx=8, pady=8)
        ttk.Button(bar, text="Annulla", command=self.cancel).pack(side="left", padx=5)
        ttk.Button(bar, text="Dimentica", command=self.ok,
                   state="normal" if self.choice.get() else "disabled").pack(side="left")
        self.bind("<Escape>", self.cancel)

    def apply(self):
        self.result = self.choice.get()


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
        ctk.CTkLabel(body, text="CercaDoc legge i documenti di queste cartelle. Con «Anche sottocartelle» spuntato "
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
        ok = ocr_tools.ocr_available()
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
        ready = models.is_installed() and semantic.libraries_available()
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
        ctk.CTkSwitch(self.adv, text=f"Apri la ricerca da qualsiasi finestra con {hotkey.LABEL}",
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
        split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
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
        config.save_config(c)
        with self.app.con:
            self.app.con.executemany("DELETE FROM esclusioni WHERE path=?", ((p,) for p in self.restore_paths))
            relations.set_rules(self.app.con, c)
        if not c["semantic_search"]:
            self.app._embedder = None
        self.app.links.reset()
        self.app._refresh_filters()
        self.app.do_search(force=True)
        self.destroy()
        self.app.reschedule_auto_scan()
        self.app.start_index(force=True)


# --------------------------------------------------------------------------
ORDERS = {"Più pertinenti": "rank", "Più recenti": "date", "Data del documento": "document_date",
          "Nome (A-Z)": "name"}
RECENT_COUNT = 15  # documenti mostrati quando la ricerca è vuota


class App(ctk.CTk):
    MODE_HITS, MODE_FULL = "Occorrenze", "Testo completo"
    MODE_LINKS = "Collegati"

    def __init__(self, quit_token=None):
        self.cfg = config.load_config()
        ctk.set_appearance_mode(self.cfg.get("theme", "system"))
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title("CercaDoc")
        self.geometry("1240x780")
        self.minsize(940, 600)
        self.configure(fg_color=PAL["bg"])
        self.con = db.connect()
        self.protection = protection.settings(config.DB_PATH)
        self._last_input = time.monotonic()
        self._history_job = None
        self._history_signature = None
        self._closing = False
        self.msgs: queue.Queue = queue.Queue()
        self._preview_seq = 0  # numero dell'anteprima richiesta: scarta i calcoli superati
        self.indexing = False
        self._quit_token = hotkey.quit_token() if quit_token is None else quit_token
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
        self._shortcut = hotkey.Registration(lambda: self.cfg.get("global_shortcut", True),
                                             hotkey.signal_window, lambda s: self.msgs.put(("shortcut_error", s)))
        self._tray = hotkey.Registration(lambda: True, lambda action: self.msgs.put(("tray", action)),
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
        if self._quit_token != hotkey.quit_token():
            self._close()
            return
        if self.protection["mode"] == "vault" and time.monotonic() - self._last_input >= 60 * self.protection["minutes"]:
            security_ui.restart(self)
            return
        try:
            for action in hotkey.WINDOW_ACTIONS:
                if hotkey.consume(action):
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
            hotkey.quit_all()
            self._close()
            return
        if action in ("pause", "resume"):
            if self._pause_job:
                self.after_cancel(self._pause_job)
                self._pause_job = None
            # Un documento grande può occupare il writer. Tk deve restare reattivo.
            if not db.try_set_paused(self.con, action == "pause"):
                self._pause_job = self.after(200, lambda: self._window_action(action))
                return
            if action == "resume":
                self.start_index(silent=True)
                hotkey.signal("agent-wake")
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
        config.save_config(self.cfg)
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
        ctk.CTkLabel(names, text="CercaDoc", font=font(24, True), text_color=PAL["text"]).pack(anchor="w")
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
            ("redact", "Oscura il testo in una copia…", lambda: features_ui.RedactDialog(self)),
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
        self.menu.add_command(label="Oscura copia…", command=lambda: features_ui.RedactDialog(self))
        self.menu.add_separator()
        self.menu.add_command(label="Dimentica questo file…", command=self._forget_result)
        self.menu.add_command(label="Dimentica la cartella che lo contiene…",
                              command=lambda: self._forget_result(folder=True))
        self.more = tk.Menu(self, tearoff=0)
        self.more.add_command(label="Cronologia e ricerche salvate…", command=lambda: features_ui.LibraryDialog(self))
        self.more.add_command(label="Mappa dei dati personali…", command=lambda: features_ui.AuditDialog(self))
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
        self.more.add_command(label="Informazioni su CercaDoc", command=lambda: AboutDialog(self))
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
        self._history_signature = features_ui.criteria(self)
        features_ui.record(self)
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
        count = relations.upcoming_count(self.con) if self.cfg.get("deadline_alerts", True) else 0
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
        return dict(ui_text.WHERE_CHOICES).get(self.where_var.get(), searcher.WHERE_ALL)

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
        filtered = bool(exts or after or before or under or (run_q and where != searcher.WHERE_ALL))
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
            con = protection.connection(config.DB_PATH)  # connessione propria: quella della finestra è sua
            self._search_con = con
            try:
                if run_q:
                    res = self._search_rows(con, run_q, exts, order, under, after, before, where)
                    searcher.add_pages(con, res)  # «Pag. N» per i passaggi trovati nei PDF
                elif filtered:
                    flt = {k: v for k, v in (("after", after), ("before", before)) if v}
                    res = searcher.list_docs(con, exts, order, under=under,
                                             flt=flt or None)
                else:
                    res = searcher.list_docs(con, None, "document_date" if order == "document_date" else "date", RECENT_COUNT)
                hint = searcher.suggest(con, run_q) if run_q and not res else None
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
        if self._embedder_failed or not semantic.available(self.cfg):
            return None
        with self._embedder_lock:
            if self._embedder is None and not self._embedder_failed:
                try:
                    from .embedder import Embedder
                    self._embedder = Embedder()
                except Exception:  # noqa: BLE001 - modello rifiutato, libreria mancante…: solo parole
                    self._embedder_failed = True
            return self._embedder

    def _search_rows(self, con, q, exts, order, under, after, before=None, where="all") -> list[dict]:
        use_meaning = order == "rank" and where != searcher.WHERE_NAME and searcher.plain_text_and_filters(q)[0]
        embedder = self._get_embedder() if use_meaning else None
        if embedder is None:
            return searcher.search(con, q, exts, order, under=under, after=after, before=before, where=where)
        return semantic.hybrid_search(con, q, embedder, ext=exts, order=order, under=under, after=after,
                                      before=before, where=where, min_score=float(self.cfg.get("semantic_min_score", semantic.MIN_SCORE)))

    def _apply_results(self, q: str, filtered: bool, short: bool, res: list[dict], hint: str | None):
        if self._history_job:
            self.after_cancel(self._history_job)
            self._history_job = None
        current = features_ui.criteria(self)
        if (q or filtered) and not short and current != self._history_signature:
            def remember():
                self._history_job = None
                if current == features_ui.criteria(self) and current != self._history_signature:
                    self._history_signature = current
                    features_ui.record(self)
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
            r["expandable"] = bool(q) and "offset" not in r and not r.get("error") and searcher.HIT_START in r.get("snippet", "")
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
            parts = preview.snippet_parts(r.get("snippet", ""))
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
                n = export.export_csv(rows, path)
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
            self.links.load(self._current()["id"], semantic.available(self.cfg))
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
                parts = (preview.anchored_parts(content, *anchor, full=True, layout=layout) if anchor else
                         preview.full_text_parts(content, terms, layout=layout))
            except Exception as ex:  # noqa: BLE001
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
        doc_id, terms, seq = self.results.results[i]["id"], searcher.query_terms(self.qvar.get()), self._search_seq

        def work():
            try:
                content, layout = preview.read_document(doc_id)
                items, total = preview.occurrences(content, terms, layout=layout)
            except Exception:  # noqa: BLE001
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
        row = searcher.docs_by_id(self.con, [doc_id]).get(doc_id)
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
        quick = preview.snippet_parts(r.get("snippet", ""))
        if quick and r.get("location"):
            quick.insert(0, (r["location"] + "\n", "title"))
        self._set_preview(quick + [("\nCarico gli altri punti in cui compare…", "dim")] if quick
                          else [("Carico l'anteprima…", "dim")])
        terms = searcher.query_terms(self.qvar.get())
        doc_id = r["id"]

        def work():
            content = layout = None
            try:
                content, layout = preview.read_document(doc_id)
                parts = (preview.anchored_parts(content, *anchor, layout=layout) if anchor else
                         preview.preview_parts(content, terms, max_n=preview.OCCURRENCES_SHOWN, layout=layout,
                                               preferred=r.get("passage_start")))
            except Exception as ex:  # noqa: BLE001
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
            except Exception as ex:  # noqa: BLE001
                messagebox.showerror("CercaDoc", f"Non riesco ad aprire il file:\n{ex}")

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
        st = agent.read_status(self.con)
        runs = {self._own_run}
        if st["phase"] in agent.ACTIVE:
            runs.add(st["run"])
        for run in runs:
            if run:
                agent.stop_file(run).touch(mode=0o600)
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
        st = agent.read_status(self.con)
        req = dict(silent=silent, force=force, retry_errors=retry_errors, target=target,
                   force_ocr=force_ocr, rebuild=rebuild, forget=forget)
        if self._worker is not None or st["phase"] in agent.ACTIVE:
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
            self._worker = agent.spawn_worker(from_window=True, run=run, retry_errors=retry_errors, target=target,
                                              force_ocr=force_ocr, rebuild=rebuild, quit_token=self._quit_token, forget=forget)
        except (OSError, RuntimeError) as exc:
            agent.launch_failed(self.con, run, str(exc))
            self.status.configure(text=str(exc))
            return
        self._own_run, self._request = run, req
        self.indexing = True
        if not silent:
            self.status.configure(text="Rimozione dall’archivio…" if forget else "Cerco i documenti nelle cartelle…")
            self._show_progress(True)

    def _sync_worker(self):
        code = self._worker.poll() if self._worker is not None else None
        st = agent.read_status(self.con)
        active = st["phase"] in agent.ACTIVE
        launch_error = None
        if code is not None:
            if self._worker.stdin:
                self._worker.stdin.close()
            if code == agent.BUSY:
                if self._request.get("forget"):
                    self._forget_queue.insert(0, self._request)
                    if active and st["phase"] != "forget":
                        self._interrupt_index()
                elif (self._request["force"] or not active) and self._reindex_pending is None:
                    self._reindex_pending = self._request
            elif code and (st["run"] != self._own_run or st["phase"] != "error"):
                launch_error = "Il lavoratore non è partito nel recinto senza rete."
                if not active:
                    agent.launch_failed(self.con, self._own_run, launch_error)
                    st = db.worker_status(self.con)
            agent.stop_file(self._own_run).unlink(missing_ok=True)
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
        except Exception as ex:  # noqa: BLE001 - un errore qui non deve fermare per sempre l'aggiornamento
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
            agent.stop_file(self._own_run).touch(mode=0o600)
            try:
                self._worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._worker.kill()
                self._worker.wait()
            if self._worker.stdin:
                self._worker.stdin.close()
            agent.stop_file(self._own_run).unlink(missing_ok=True)
        try:
            self.con.close()
        except Exception:
            pass
        self.destroy()


def run(configure=False, action="open", quit_token=None) -> None:
    from .agent import acquire_lock
    token = hotkey.quit_token()
    if quit_token is not None and token != quit_token:
        return
    lock = acquire_lock(config.APP_DIR / "window.lock")
    if lock is None:
        hotkey.signal(action)
        hotkey.signal_window()
        return
    with lock:
        try:
            if sys.platform == "win32":
                (config.APP_DIR / "window.pid").write_text(str(os.getpid()), encoding="ascii")
            if not security_ui.startup(configure) or token != hotkey.quit_token():
                return
            with acquire_lock(config.APP_DIR / "window-tray.lock", blocking=True):
                app = App(token)
                if sys.platform == "darwin":
                    app.createcommand("::tk::mac::ReopenApplication", lambda: app._window_action("open"))
                if action != "open":
                    app.after_idle(lambda: app._window_action(action))
                app.mainloop()
        except (RuntimeError, OSError) as exc:
            messagebox.showerror("Apertura di CercaDoc", str(exc))
        finally:
            if sys.platform == "win32":
                (config.APP_DIR / "window.pid").unlink(missing_ok=True)
            protection.forget()
