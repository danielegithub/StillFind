# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Oscuramento di una copia del documento."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from stillfind.core import facts
from stillfind.tools.redaction import Redactor


class RedactDialog(tk.Toplevel):
    def __init__(self, app):
        row = app._current()
        if not row:
            messagebox.showinfo("Copia oscurata","Seleziona un documento.",parent=app)
            return
        self.path = Path(row["path"])
        if self.path.suffix.lower() not in {".pdf",".txt",".md",".csv",".log",".tex",".json",".xml"}:
            messagebox.showinfo("Copia oscurata","Scegli un PDF o un file di testo.",parent=app)
            return
        super().__init__(app)
        self.app, self.pdf = app,None
        self.geometry("790x730")
        self.title("Copia oscurata · " + self.path.name)
        self.transient(app)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW",self.cancel)
        self.choices = ttk.Frame(self)
        self.choices.pack(fill="x",padx=10,pady=6)
        self.flags = {}
        for kind in ("cf","iban","email","piva"):
            var = tk.BooleanVar()
            self.flags[kind] = var
            ttk.Checkbutton(self.choices,text=facts.ENTITY_LABELS[kind],variable=var).pack(side="left")
        self.values = tk.Listbox(self, height=3, selectmode="extended", exportselection=False)
        entities = app.con.execute("""SELECT DISTINCT e.etichetta FROM entita e JOIN menzioni m ON e.id=m.entita_id
            JOIN passaggi p ON p.id=m.passaggio_id WHERE p.doc_id=? AND e.tipo IN ('cf','piva','email','iban','soggetto')
            ORDER BY e.etichetta""", (row["id"],))
        for (label,) in entities:
            self.values.insert("end",label)
        if self.values.size():
            ttk.Label(self,text="Singoli valori da oscurare (selezione multipla)").pack(anchor="w",padx=10)
            self.values.pack(fill="x",padx=10)
        ttk.Label(self,text="Termini o singoli valori da oscurare, uno per riga").pack(anchor="w",padx=10)
        self.terms = tk.Text(self,height=3)
        self.terms.pack(fill="x",padx=10)
        try:
            self.terms.insert("1.0", app.prev._textbox.get("sel.first","sel.last"))
        except tk.TclError:
            pass
        from stillfind.extractors.ocr import Tesseract
        installed = Tesseract.available()
        self.use_ocr = tk.BooleanVar(value=installed)
        if self.path.suffix.lower() == ".pdf":
            label = "Rileva anche i dati nelle immagini con OCR" if installed else "Tesseract assente · controlla le immagini e oscura le aree a mano"
            ttk.Checkbutton(self,text=label,variable=self.use_ocr,state="normal" if installed else "disabled").pack(anchor="w",padx=10)
        self.note = ttk.Label(self,text="Per i PDF puoi aggiungere aree a mano su ogni pagina.")
        self.note.pack(anchor="w",padx=10,pady=4)
        self.canvas = tk.Canvas(self,background="#555555",highlightthickness=0)
        self.canvas.pack(fill="both",expand=True,padx=10)
        self.canvas.bind("<ButtonPress-1>",self.begin)
        self.canvas.bind("<B1-Motion>",self.drag)
        self.canvas.bind("<ButtonRelease-1>",self.end)
        bar = ttk.Frame(self)
        bar.pack(fill="x",padx=10,pady=8)
        ttk.Button(bar,text="Annulla",command=self.cancel).pack(side="right")
        self.action = ttk.Button(bar,text="Prepara copia",command=self.start)
        self.action.pack(side="right",padx=8)
        self.image = self.origin = None
        self.busy = self.cancelled = False
        self.manual = []

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
                    messagebox.showerror("Copia oscurata",str(exc),parent=self)
                    return
                messagebox.showinfo("Copia oscurata",f"{n} occorrenze sostituite con [OMISSIS].",parent=self)
                self.destroy()
            return
        try:
            self.pdf = Redactor.PdfReview(self.path)
        except Exception as exc:
            messagebox.showerror("Apertura PDF",str(exc),parent=self)
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
        self.note.configure(text=f"Lettura della pagina {self.pdf.done+1} di {self.pdf.pages}…")
        q = queue.Queue()
        lang = self.app.cfg["ocr_lang"] if self.use_ocr.get() else None
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
                messagebox.showerror("Oscuramento PDF",res,parent=self)
                self.cancel()
            else:
                from PIL import ImageTk
                self.image,self.size,self.detected = res
                thumb = self.image.copy()
                thumb.thumbnail((max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height())))
                self.photo = ImageTk.PhotoImage(thumb,master=self)
                self.sx,self.sy = self.image.width/thumb.width,self.image.height/thumb.height
                self.canvas.create_image(0,0,image=self.photo,anchor="nw")
                self.note.configure(text=f"Pagina {self.pdf.done+1}/{self.pdf.pages} · controlla i dati "
                                         "ancora visibili e trascina per coprirli.")
                self.action.configure(text="Approva pagina",state="normal",command=self.approve)
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
            self.note.configure(text="Nessun dato selezionato. Trascina per oscurare una zona o annulla la copia.")
            return
        self.pdf.approve(self.image,self.size,self.manual,self.detected)
        self.image = None
        if self.pdf.done < self.pdf.pages:
            self.prepare()
        else:
            self.action.configure(text="Salva copia oscurata…",command=self.save)

    def destination(self):
        ext = self.path.suffix
        return filedialog.asksaveasfilename(parent=self, title="Salva come nuovo file",
                    defaultextension=ext,initialfile=self.path.stem+"-oscurato"+ext)

    def save(self):
        dest = self.destination()
        if dest:
            try:
                self.pdf.save(dest)
            except Exception as exc:
                messagebox.showerror("Copia oscurata",str(exc),parent=self)
                return
            self.cancel()
