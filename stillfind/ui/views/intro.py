# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Presentazione iniziale: i vantaggi di StillFind con tre brevi dimostrazioni (esempi inventati)."""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from stillfind.core.i18n import tr
from stillfind.ui.brand import Brand
from stillfind.ui.theme import PAL, card, col, fit_width, font, ghost_button, primary_button


class IntroView(ctk.CTkFrame):
    """Quattro schermate (apertura e tre dimostrazioni). Si avanza solo con i pulsanti, mai da sole."""

    PAGES = ("hero", "words", "concept", "private")
    STEP_MS, FADE_STEPS = 28, 8

    def __init__(self, app, on_done):
        super().__init__(app, fg_color=PAL["bg"], corner_radius=0, border_width=0)
        self.on_done = on_done
        self.index = 0
        self._jobs: list[str] = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        top.grid(row=0, column=0, sticky="ew", padx=32, pady=(20, 0))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="", image=Brand.wordmark(130)).grid(row=0, column=0)
        self.dots = ctk.CTkLabel(top, text="", font=font(13), text_color=PAL["muted"])
        self.dots.grid(row=0, column=2)
        self.stage = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        self.stage.grid(row=1, column=0, sticky="nsew", padx=32, pady=12)
        self.stage.grid_columnconfigure(0, weight=1)
        self.stage.grid_rowconfigure(0, weight=1)
        bar = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        bar.grid(row=2, column=0, sticky="ew", padx=32, pady=(0, 24))
        bar.grid_columnconfigure(1, weight=1)
        self.back_button = ghost_button(bar, tr("Indietro"), self.back, width=110)
        self.skip_button = ctk.CTkButton(bar, text=tr("Salta presentazione"), command=self.skip, width=0, height=28,
                                         corner_radius=0, fg_color="transparent", hover_color=PAL["ghost_hover"],
                                         text_color=PAL["muted"], font=font(12), border_width=0)
        self.next_button = primary_button(bar, tr("Avanti"), self.next, width=190)
        self.next_button.configure(height=40)
        self.back_button.grid(row=0, column=0, sticky="w")
        self.skip_button.grid(row=0, column=1)
        self.next_button.grid(row=0, column=2, sticky="e")
        for button in (self.back_button, self.next_button, self.skip_button):
            tk.Frame.configure(button, takefocus=True)
            for key in ("<space>", "<Return>"):
                tk.Misc.bind(button, key, lambda e, b=button: (b.invoke(), "break")[1])
        self.bind("<Destroy>", lambda e: self._cancel() if e.widget is self else None)
        self._show(0)

    # ---- navigazione
    def next(self):
        if self.index >= len(self.PAGES) - 1:
            self.finish()
        else:
            self._show(self.index + 1)

    def back(self):
        if self.index > 0:
            self._show(self.index - 1)

    def skip(self):
        self.finish()

    def finish(self):
        self._cancel()
        self.on_done()

    def _show(self, index: int):
        self._cancel()
        self.index = index
        for child in self.stage.winfo_children():
            child.destroy()
        page = self.PAGES[index]
        getattr(self, f"_page_{page}")(self.stage)
        last, hero = index == len(self.PAGES) - 1, index == 0
        self.dots.configure(text="" if hero else tr("Passo {i} di {n}", i=index, n=len(self.PAGES) - 1))
        if hero:
            self.back_button.grid_remove()
            self.skip_button.grid_remove()
            self.next_button.configure(text=tr("Scopri StillFind"))
        else:
            self.back_button.grid()
            self.skip_button.grid()
            self.next_button.configure(text=tr("Prepariamo il tuo archivio") if last else tr("Avanti"))
        self.next_button.configure(width=fit_width(self.next_button.cget("text"), 190, True))
        self.after_idle(lambda: self.next_button.winfo_exists() and tk.Misc.focus_set(self.next_button))

    # ---- animazioni brevi
    @staticmethod
    def blend(start: str, end: str, t: float) -> str:
        """Colore intermedio fra due colori «#rrggbb»."""
        a = [int(start[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))

    def _later(self, ms, command):
        job = self.after(ms, command)
        self._jobs.append(job)

    def _cancel(self):
        for job in self._jobs:
            try:
                self.after_cancel(job)
            except (tk.TclError, ValueError):
                pass
        self._jobs.clear()

    def _fade_in(self, label: ctk.CTkLabel, name: str = "text", delay: int = 0):
        """Il testo compare con una breve dissolvenza dallo sfondo al colore finale."""
        background, target = col("card"), col(name)
        label.configure(text_color=background)

        def step(i):
            if not label.winfo_exists():
                return
            label.configure(text_color=self.blend(background, target, i / self.FADE_STEPS))
            if i < self.FADE_STEPS:
                self._later(self.STEP_MS, lambda: step(i + 1))
        self._later(delay, lambda: step(1))

    def _type(self, label: ctk.CTkLabel, text: str, then=None, delay: int = 250):
        def step(i):
            if not label.winfo_exists():
                return
            label.configure(text=text[:i] + ("▏" if i < len(text) else ""))
            if i < len(text):
                self._later(55, lambda: step(i + 1))
            elif then:
                self._later(260, then)
        self._later(delay, lambda: step(1))

    # ---- pagine
    def _page_hero(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0, border_width=0)
        box.grid(row=0, column=0)
        ctk.CTkLabel(box, text="", image=Brand.wordmark(300)).grid(row=0, column=0, pady=(0, 22))
        title = ctk.CTkLabel(box, text=tr("I tuoi documenti. Finalmente ritrovati."), font=font(32, True),
                             text_color=PAL["text"], wraplength=720, justify="center")
        title.grid(row=1, column=0, pady=(0, 18))
        sub = ctk.CTkLabel(box, text=tr("Trova quello che cerchi dentro PDF, documenti, email e scansioni, "
                                        "anche quando non ricordi il nome del file."),
                           font=font(15), text_color=PAL["muted"], wraplength=600, justify="center")
        sub.grid(row=2, column=0, pady=(0, 26))
        link = ctk.CTkButton(box, text=tr("Vai alla configurazione"), command=self.finish, width=0, height=28,
                             corner_radius=0, fg_color="transparent", hover_color=PAL["ghost_hover"],
                             text_color=PAL["muted"], font=font(12), border_width=0)
        link.grid(row=3, column=0)
        self.link = link
        self._fade_in(title, "text")
        self._fade_in(sub, "muted", delay=250)

    def _demo(self, parent, title, lead):
        head = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0, border_width=0)
        head.grid(row=0, column=0, sticky="n", pady=(10, 0))
        t = ctk.CTkLabel(head, text=title, font=font(24, True), text_color=PAL["text"], wraplength=720)
        t.grid(row=0, column=0, pady=(0, 6))
        s = ctk.CTkLabel(head, text=lead, font=font(14), text_color=PAL["muted"], wraplength=640, justify="center")
        s.grid(row=1, column=0)
        body = card(parent)
        body.grid(row=1, column=0, pady=(18, 0), ipadx=6, ipady=6)
        parent.grid_rowconfigure(0, weight=0)
        parent.grid_rowconfigure(1, weight=1)
        self._fade_in(t)
        return body

    def _mock_search(self, body, query, on_typed):
        tag = ctk.CTkLabel(body, text=tr("Esempio dimostrativo"), font=font(11, True), text_color=PAL["muted"], anchor="w")
        tag.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))
        frame = ctk.CTkFrame(body, fg_color=PAL["field"], corner_radius=0, border_width=1, border_color=PAL["line"])
        frame.grid(row=1, column=0, sticky="ew", padx=16)
        field = ctk.CTkLabel(frame, text="", font=font(15), text_color=PAL["text"], anchor="w", height=38, width=480)
        field.pack(fill="x", padx=14)
        self._type(field, query, on_typed)
        return field

    def _mock_document(self, body, name, lines, page=None):
        frame = ctk.CTkFrame(body, fg_color=PAL["field"], corner_radius=0, border_width=1, border_color=PAL["line"])
        frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(12, 8))
        ctk.CTkLabel(frame, text=name + (f"  ·  {page}" if page else ""), font=font(12, True),
                     text_color=PAL["muted"], anchor="w").pack(fill="x", padx=16, pady=(12, 4))
        text = tk.Text(frame, height=len(lines) + 2, width=58, wrap="word", borderwidth=0, highlightthickness=0,
                       background=col("field"), foreground=col("text"), font=("", 13), padx=16, pady=10, cursor="arrow", spacing1=2, spacing3=4)
        text.pack(fill="x", padx=2, pady=(0, 8))
        text.insert("1.0", "\n".join(lines))
        text.tag_configure("hit", background=col("hit_bg"), foreground=col("hit_fg"))
        text.configure(state="disabled")
        return text

    def _highlight(self, text: tk.Text, needle: str):
        start = text.search(needle, "1.0", nocase=True)
        if start:
            text.tag_add("hit", start, f"{start}+{len(needle)}c")

    def _page_words(self, parent):
        body = self._demo(parent, tr("Cerca dentro i documenti"),
                          tr("Scrivi una parola o una frase: StillFind la trova nel testo, anche all’interno di un PDF."))
        needle = tr("canone mensile")
        lines = [tr("Contratto di locazione ad uso abitativo"),
                 tr("Art. 3 — Il canone mensile è fissato in 650,00 euro, da versare entro il 5 di ogni mese."),
                 tr("Art. 4 — Le spese condominiali sono a carico del conduttore.")]
        holder = {}

        def show():
            holder["doc"] = self._mock_document(body, "contratto-affitto.pdf", lines, tr("Pag. {number}", number=3))
            self._later(300, lambda: self._highlight(holder["doc"], needle))
        self._mock_search(body, needle, show)

    def _page_concept(self, parent):
        body = self._demo(parent, tr("Ritrova anche un concetto"),
                          tr("Anche con parole diverse da quelle del documento, StillFind trova il passaggio che parla della stessa cosa."))
        query = tr("spese riscaldamento")
        lines = [tr("Ripartizione caldaia: quota a carico di ciascun inquilino per la stagione invernale.")]
        note = ctk.CTkLabel(body, text=tr("Serve la ricerca per significato: puoi attivarla durante la configurazione."),
                            font=font(12), text_color=PAL["muted"], anchor="w", wraplength=520, justify="left")

        def show():
            doc = self._mock_document(body, "condominio-2026.pdf", lines, tr("Pag. {number}", number=2))
            ctk.CTkLabel(body, text="≈ " + tr("trovato per significato"), font=font(12, True),
                         text_color=PAL["accent"], anchor="w").grid(row=3, column=0, sticky="w", padx=16)
            note.grid(row=4, column=0, sticky="w", padx=16, pady=(4, 12))
            self._fade_in(note, "muted", delay=200)
        self._mock_search(body, query, show)

    def _page_private(self, parent):
        body = self._demo(parent, tr("I tuoi documenti restano sul tuo computer"),
                          tr("StillFind lavora in locale: niente caricamenti, niente account."))
        tag = ctk.CTkLabel(body, text=tr("Esempio dimostrativo"), font=font(11, True), text_color=PAL["muted"], anchor="w")
        tag.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))
        row = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0, border_width=0)
        row.grid(row=1, column=0, padx=16, pady=(4, 16))
        items = ((tr("Elaborazione locale"), tr("Leggere e indicizzare avviene su questo computer.")),
                 (tr("Funziona offline"), tr("Nessuna connessione a internet: nemmeno per la ricerca per significato.")),
                 (tr("Archivio cifrabile"), tr("Se vuoi, proteggi l’archivio con la cifratura. I documenti originali restano intatti.")))
        for column, (heading, detail) in enumerate(items):
            cell = ctk.CTkFrame(row, fg_color=PAL["field"], corner_radius=0, border_width=1, border_color=PAL["line"],
                                width=230)
            cell.grid(row=0, column=column, padx=6, sticky="nsew")
            ctk.CTkFrame(cell, fg_color=PAL["accent"], height=4, corner_radius=0).pack(fill="x")
            h = ctk.CTkLabel(cell, text=heading, font=font(14, True), text_color=PAL["text"], wraplength=190, anchor="w",
                             justify="left")
            h.pack(fill="x", padx=16, pady=(14, 6))
            d = ctk.CTkLabel(cell, text=detail, font=font(12), text_color=PAL["muted"], wraplength=190, anchor="w",
                             justify="left")
            d.pack(fill="x", padx=16, pady=(0, 16))
            self._fade_in(h, "text", delay=250 * column)
            self._fade_in(d, "muted", delay=250 * column + 120)
