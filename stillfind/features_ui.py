# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Finestre per le ricerche personali, l'audit e la copia oscurata."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox

from . import config, db, facts, library, redaction


def criteria(app):
    return dict(query=app.qvar.get().strip(), category=app.type_var.get(), where=app.where_var.get(),
                order=app.order_var.get(), date=app.date_var.get(), custom=app._date_custom,
                folder=app.folder_filter)


def record(app):
    from .ui_text import ALL_TYPES, ANY_DATE
    c = criteria(app)
    if c["query"] or c["folder"] or c["category"] != ALL_TYPES or c["date"] != ANY_DATE:
        if app.cfg.get("search_history", True):
            library.save_search(app.con, c)


def restore(app, c):
    from .ui_text import ALL_FOLDERS
    app.type_var.set(c["category"])
    app.where_var.set(c["where"])
    app.order_var.set(c["order"])
    app.date_var.set(c["date"])
    app._date_custom = c["custom"]
    app.folder_filter = c["folder"]
    label = next((k for k,v in app._folder_map.items() if v == c["folder"]), c["folder"] or ALL_FOLDERS)
    app.folder_var.set(label)
    app.qvar.set(c["query"])
    app.do_search(force=True)


class LibraryDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Cronologia e ricerche salvate")
        self.geometry("660x420")
        self.transient(app)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=8)
        self.lists, self.values = [], {}
        for title in ("Cronologia", "Ricerche salvate"):
            tree = ttk.Treeview(self.tabs, show="headings", columns=("label","query"))
            tree.heading("label", text="Ricerca")
            tree.heading("query", text="Testo")
            self.tabs.add(tree, text=title)
            self.lists.append(tree)
            tree.bind("<Double-1>", lambda _: self.open())
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=5)
        for text, cmd in (("Apri",self.open),("Salva ricerca corrente…",self.save),
                          ("Elimina",self.remove),("Svuota cronologia",self.clear)):
            ttk.Button(bar,text=text,command=cmd).pack(side="left")
        self.enabled = tk.BooleanVar(value=app.cfg.get("search_history",True))
        ttk.Checkbutton(self,text="Ricorda le mie ricerche su questo computer",variable=self.enabled,
                        command=self.toggle).pack(anchor="w",padx=12,pady=6)
        self.refresh()

    def refresh(self):
        self.values.clear()
        for i, tree in enumerate(self.lists):
            tree.delete(*tree.get_children())
            for ident, name, c in library.searches(self.app.con, bool(i)):
                tree.insert("", "end", iid=str(ident), values=(name or c["folder"] or c["category"], c["query"]))
                self.values[str(ident)] = c

    def selection(self):
        return self.lists[self.tabs.index("current")].selection()

    def open(self):
        sel = self.selection()
        if sel:
            c = self.values[sel[0]]
            self.destroy()
            restore(self.app,c)

    def save(self):
        name = simpledialog.askstring("Salva ricerca","Nome della ricerca",parent=self)
        if name and name.strip():
            library.save_search(self.app.con, criteria(self.app), name)
            self.refresh()
            self.tabs.select(1)

    def remove(self):
        sel = self.selection()
        if sel:
            with self.app.con:
                self.app.con.execute("DELETE FROM ricerche WHERE id=?", (sel[0],))
            self.refresh()

    def clear(self):
        if messagebox.askyesno("Cronologia","Cancellare le ricerche recenti?",parent=self):
            self.app._history_signature = criteria(self.app)
            with self.app.con:
                self.app.con.execute("DELETE FROM ricerche WHERE nome IS NULL")
            self.refresh()

    def toggle(self):
        self.app.cfg["search_history"] = self.enabled.get()
        config.save_config(self.app.cfg)


class AuditDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Mappa dei dati personali")
        self.geometry("920x480")
        self.transient(app)
        bar = ttk.Frame(self)
        bar.pack(fill="x",padx=10,pady=8)
        ttk.Label(bar,text="Identificativo del soggetto").pack(side="left")
        self.query = ttk.Entry(bar)
        self.query.pack(side="left",padx=5,fill="x",expand=True)
        self.query.bind("<Return>",lambda _: self.scan())
        ttk.Button(bar,text="Cerca nell’archivio",command=self.scan).pack(side="left")
        ttk.Button(bar,text="Desktop e Download",command=self.exposure).pack(side="left")
        ttk.Button(bar,text="Controlla cartella…",command=self.folder).pack(side="left")
        table = ttk.Frame(self)
        table.pack(fill="both",expand=True,padx=10)
        self.tree = ttk.Treeview(table, columns=("name","kind","value","folder"), show="headings")
        for k,t in zip(self.tree["columns"],("Documento","Tipo","Valore trovato","Cartella")):
            self.tree.heading(k,text=t)
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both",expand=True)
        self.tree.bind("<Double-1>", lambda _: self.open())
        self.note = ttk.Label(self,text="Sono considerate le entità abilitate e il testo già indicizzato.")
        self.note.pack(anchor="w",padx=10,pady=5)
        self.items = {}
        self.seq = 0
        self.scan()

    def exposure(self):
        folders = library.exposure_folders()
        if folders:
            self.scan(folders)
        else:
            self.note.configure(text="Nessuna cartella Desktop o Download disponibile.")

    def folder(self):
        folder = filedialog.askdirectory(parent=self)
        if folder:
            self.scan([folder])

    def scan(self, folders=()):
        result = queue.Queue()
        self.seq += 1
        seq, query = self.seq, self.query.get()
        self.note.configure(text="Ricerca delle menzioni nell’archivio…")
        def work():
            try:
                with closing(db.connect()) as con:
                    rows = library.audit(con,query,folders)
                    issues = con.execute("SELECT count(*) FROM docs WHERE error<>'' "
                                         "OR (ext='pdf' AND (notext=1 OR blank_pages>0))").fetchone()[0]
                    result.put((rows, issues))
            except Exception as exc:
                result.put(str(exc))
        threading.Thread(target=work,daemon=True).start()
        def poll():
            if seq != self.seq:
                return
            try:
                rows = result.get_nowait()
            except queue.Empty:
                self.after(100,poll)
                return
            self.tree.delete(*self.tree.get_children())
            self.items.clear()
            if isinstance(rows,str):
                self.note.configure(text=rows)
                return
            rows, issues = rows
            def display(offset=0):
                if seq != self.seq:
                    return
                for doc,name,path,kind,value,start,count,end in rows[offset:offset+180]:
                    label = facts.ENTITY_LABELS.get(kind, "Testo esatto")
                    row = self.tree.insert("", "end",values=(name,label,value,Path(path).parent))
                    self.items[row] = doc,start,end
                if offset+180 < len(rows):
                    self.after(1, lambda: display(offset+180))
                else:
                    files = len({row[0] for row in rows})
                    coverage = f" · {issues} file nell’archivio da verificare in Diagnostica" if issues else ""
                    self.note.configure(text=f"{files} file con riscontri nel testo indicizzato{coverage} · "
                                             "doppio clic per aprire il passaggio")
            display()
        self.after(10,poll)

    def open(self):
        sel = self.tree.selection()
        if sel:
            self.app.open_indexed(*self.items[sel[0]])
            self.destroy()


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
        from .extractors.ocr import ocr_available
        installed = ocr_available()
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
                    n = redaction.redact_text(self.path,dest,self.types,self.words)
                except Exception as exc:
                    messagebox.showerror("Copia oscurata",str(exc),parent=self)
                    return
                messagebox.showinfo("Copia oscurata",f"{n} occorrenze sostituite con [OMISSIS].",parent=self)
                self.destroy()
            return
        try:
            self.pdf = redaction.PdfReview(self.path)
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
