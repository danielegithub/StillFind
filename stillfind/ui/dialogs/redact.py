# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Oscuramento di una copia del documento."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from stillfind.core import facts

from stillfind.core.ocr_languages import OcrLanguages
from stillfind.core.i18n import tr, trn
from stillfind.tools.redaction import Redactor
from stillfind.ui.theme import PAL, col, font, ghost_button, primary_button
from stillfind.ui.widgets import SquareDialog, SquareCheckBox


class RedactDialog(SquareDialog):
    def __init__(self, app):
        row = app._current()
        if not row:
            messagebox.showinfo(tr("Copia oscurata"), tr("Seleziona un documento."), parent=app)
            return
        self.path = Path(row["path"])
        if self.path.suffix.lower() not in {".pdf", ".txt", ".md", ".csv", ".log", ".tex", ".json", ".xml"}:
            messagebox.showinfo(tr("Copia oscurata"), tr("Scegli un PDF o un file di testo."), parent=app)
            return
        super().__init__(app, tr("Copia oscurata") + " · " + self.path.name, "820x700", minsize=(700, 600), modal=True)
        self.app, self.pdf = app, None
        pad = self.MARGIN
        self.action = primary_button(self, tr("Prepara copia"), self.start, width=160)
        self.button_bar(self.action, ghost_button(self, tr("Annulla"), self.cancel, width=100))
        self.choices = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        self.choices.pack(fill="x", padx=pad, pady=(pad, 6))
        self.flags = {}
        for kind in ("cf", "iban", "email", "piva"):
            var = tk.BooleanVar()
            self.flags[kind] = var
            SquareCheckBox(self.choices, text=facts.entity_label(kind), variable=var, font=font(13),
                            checkbox_width=20, checkbox_height=20).pack(side="left", padx=(0, 18))
        entities = app.con.execute("""SELECT DISTINCT e.etichetta FROM entita e JOIN menzioni m ON e.id=m.entita_id
            JOIN passaggi p ON p.id=m.passaggio_id WHERE p.doc_id=? AND e.tipo IN ('cf','piva','email','iban','soggetto')
            ORDER BY e.etichetta""", (row["id"],)).fetchall()
        self.values = tk.Listbox(self, height=4, selectmode="extended", exportselection=False, activestyle="none",
                                 background=col("field"), foreground=col("text"), font=("", 13),
                                 selectbackground=col("sel"), selectforeground=col("text"), borderwidth=0,
                                 highlightthickness=1, highlightbackground=col("line"), highlightcolor=col("focus"))
        for (label,) in entities:
            self.values.insert("end", label)
        if self.values.size():
            self._label(tr("Singoli valori da oscurare (selezione multipla)"))
            self.values.pack(fill="x", padx=pad, pady=(0, 6))
        self._label(tr("Termini o singoli valori da oscurare, uno per riga"))
        self.terms = ctk.CTkTextbox(self, height=76, font=font(13), border_width=1, border_color=PAL["line"])
        self.terms.pack(fill="x", padx=pad)
        self.terms._textbox.configure(padx=8, pady=6)
        try:
            self.terms.insert("1.0", app.prev._textbox.get("sel.first", "sel.last"))
        except tk.TclError:
            pass
        from stillfind.extractors.ocr import Tesseract
        installed = Tesseract.available()
        self.use_ocr = tk.BooleanVar(value=installed)
        if self.path.suffix.lower() == ".pdf":
            label = (tr("Rileva anche i dati nelle immagini con OCR") if installed
                     else tr("Tesseract assente · controlla le immagini e oscura le aree a mano"))
            SquareCheckBox(self, text=label, variable=self.use_ocr, font=font(13), checkbox_width=20,
                            checkbox_height=20, state="normal" if installed else "disabled"
                            ).pack(anchor="w", padx=pad, pady=(10, 0))
        self.note = ctk.CTkLabel(self, text=tr("Per i PDF puoi aggiungere aree a mano su ogni pagina."), font=font(12),
                                 text_color=PAL["muted"], anchor="w", justify="left", wraplength=760)
        self.note.pack(fill="x", padx=pad, pady=(10, 6))
        stage = ctk.CTkFrame(self, corner_radius=0, fg_color=PAL["card"], border_width=1, border_color=PAL["line"])
        stage.pack(fill="both", expand=True, padx=pad)
        self.canvas = tk.Canvas(stage, background="#555555", highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True, padx=1, pady=1)
        self.canvas.bind("<ButtonPress-1>", self.begin)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.end)
        self.image = self.origin = None
        self.busy = self.cancelled = False
        self.manual = []

    def _label(self, text):
        ctk.CTkLabel(self, text=text, font=font(13, True), text_color=PAL["text"], anchor="w"
                     ).pack(anchor="w", padx=self.MARGIN, pady=(8, 4))

    def close(self):
        self.cancel()

    def cancel(self):
        self.cancelled = True
        if self.busy:
            self.withdraw()
            return
        if self.pdf:
            self.pdf.close()
        self.destroy()

    def start(self):
        self.types = [k for k,v in self.flags.items() if v.get()]
        self.words = self.terms.get("1.0","end").splitlines()
        self.words.extend(self.values.get(i) for i in self.values.curselection())
        if self.path.suffix.lower() != ".pdf":
            dest = self.destination()
            if dest:
                try:
                    n = Redactor.redact_text(self.path,dest,self.types,self.words)
                except Exception as exc:
                    messagebox.showerror(tr("Copia oscurata"),str(exc),parent=self)
                    return
                messagebox.showinfo(tr("Copia oscurata"),
                                    trn("{n} occorrenza sostituita con [OMISSIS].", "{n} occorrenze sostituite con [OMISSIS].", n),
                                    parent=self)
                self.destroy()
            return
        try:
            self.pdf = Redactor.PdfReview(self.path)
        except Exception as exc:
            messagebox.showerror(tr("Apertura PDF"),str(exc),parent=self)
            return
        for w in self.choices.winfo_children():
            w.configure(state="disabled")
        self.terms.configure(state="disabled")
        self.prepare()

    def prepare(self):
        self.image, self.origin = None,None
        self.manual = []
        self.canvas.delete("all")
        self.action.configure(state="disabled")
        self.note.configure(text=tr("Lettura della pagina {page} di {total}…", page=self.pdf.done+1, total=self.pdf.pages))
        q = queue.Queue()
        lang = OcrLanguages.normalize(self.app.cfg.get("ocr_lang")) if self.use_ocr.get() else None
        self.busy = True
        def work():
            try:
                q.put(self.pdf.prepare(self.types,self.words,lang))
            except Exception as exc:
                q.put(str(exc))
        threading.Thread(target=work,daemon=True).start()
        def poll():
            try:
                res = q.get_nowait()
            except queue.Empty:
                self.after(100,poll)
                return
            self.busy = False
            if self.cancelled:
                self.cancel()
            elif isinstance(res,str):
                messagebox.showerror(tr("Oscuramento PDF"),res,parent=self)
                self.cancel()
            else:
                from PIL import ImageTk
                self.image,self.size,self.detected = res
                thumb = self.image.copy()
                thumb.thumbnail((max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height())))
                self.photo = ImageTk.PhotoImage(thumb,master=self)
                self.sx,self.sy = self.image.width/thumb.width,self.image.height/thumb.height
                self.canvas.create_image(0,0,image=self.photo,anchor="nw")
                self.note.configure(text=tr("Pagina {page}/{total} · controlla i dati ancora visibili e trascina per coprirli.",
                                            page=self.pdf.done+1, total=self.pdf.pages))
                self.action.configure(text=tr("Approva pagina"),state="normal",command=self.approve)
        self.after(30,poll)

    def begin(self,e):
        if self.image:
            self.origin = (e.x,e.y)
            self.box = self.canvas.create_rectangle(e.x,e.y,e.x,e.y,fill="black",outline="black")

    def drag(self,e):
        if self.origin:
            self.canvas.coords(self.box,*self.origin,e.x,e.y)

    def end(self,e):
        if self.origin:
            x,y = self.origin
            self.drag(e)
            self.origin = None
            if abs(x-e.x)>1 and abs(y-e.y)>1:
                self.manual.append((x*self.sx,y*self.sy,e.x*self.sx,e.y*self.sy))

    def approve(self):
        if self.pdf.done + 1 == self.pdf.pages and not (self.detected or self.manual or self.pdf.marks):
            self.note.configure(text=tr("Nessun dato selezionato. Trascina per oscurare una zona o annulla la copia."))
            return
        self.pdf.approve(self.image,self.size,self.manual,self.detected)
        self.image = None
        if self.pdf.done < self.pdf.pages:
            self.prepare()
        else:
            self.action.configure(text=tr("Salva copia oscurata…"),command=self.save)

    def destination(self):
        ext = self.path.suffix
        return filedialog.asksaveasfilename(parent=self, title=tr("Salva come nuovo file"),
                    defaultextension=ext,initialfile=self.path.stem+tr("-oscurato")+ext)

    def save(self):
        dest = self.destination()
        if dest:
            try:
                self.pdf.save(dest)
            except Exception as exc:
                messagebox.showerror(tr("Copia oscurata"),str(exc),parent=self)
                return
            self.cancel()
