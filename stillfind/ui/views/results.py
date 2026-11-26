# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Elenco dei risultati."""
from __future__ import annotations

import sys
import tkinter as tk

import customtkinter as ctk
from PIL import ImageTk

from stillfind.core import labels as ui_text
from stillfind.core.i18n import tr
from stillfind.ui.theme import col, dark, icon_pil
from stillfind.ui.widgets import HoverTip


class ResultList(ctk.CTkFrame):
    """Elenco dei risultati: titolo, cartella, data e la frase in cui compare la parola cercata.

    È un solo widget Text (veloce anche con 500 righe). Selezione con clic, Maiusc per un intervallo,
    ⌘/Ctrl per aggiungere; frecce, Invio, doppio clic."""

    BADGE_SIZE = 16   # lato dell'icona dello stato dei vettori, in pixel

    def __init__(self, master, on_change, on_open, on_menu, on_copy):
        super().__init__(master, fg_color="transparent", corner_radius=0, border_width=0)
        self._photos: dict[tuple[str, bool], ImageTk.PhotoImage] = {}  # Tk non conserva i riferimenti alle immagini
        self._tip_for: int | None = None
        self._pointer = (0, 0)
        self.on_change, self.on_open, self.on_menu, self.on_copy = on_change, on_open, on_menu, on_copy
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.text = tk.Text(self, wrap="word", cursor="arrow", borderwidth=0, highlightthickness=1, padx=0, pady=8,
                            exportselection=False, takefocus=True, undo=False, insertwidth=0, spacing1=0, spacing3=0)
        self.text.grid(row=0, column=0, sticky="nsew")
        sb = ctk.CTkScrollbar(self, command=self.text.yview, corner_radius=0, border_spacing=0, width=12)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, 2), pady=2)
        self.tip = HoverTip(self.text)
        self.text.configure(yscrollcommand=lambda *args: (sb.set(*args), self._hide_tip()))
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
        t.bind("<Motion>", self._motion, add="+")
        for seq in ("<Leave>", "<MouseWheel>", "<Button-4>", "<Button-5>", "<Key>"):
            t.bind(seq, lambda e: self._hide_tip(), add="+")
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
                    selectforeground=col("text"), inactiveselectbackground=col("card"),
                    highlightbackground=col("card"), highlightcolor=col("focus"))
        inset, nested = 14, 28  # margine sinistro: il testo non tocca il bordo né l'evidenziazione della riga
        margins = dict(lmargin1=inset, lmargin2=inset, rmargin=inset)
        t.tag_configure("pick", background=col("sel"), **margins)
        t.tag_configure("icon", font=("", 9, "bold"), foreground=col("muted"), **margins)
        t.tag_configure("title", font=("", 12, "bold"), foreground=col("text"), **margins)
        t.tag_configure("meta", font=("", 10), foreground=col("muted"), spacing3=4, **margins)
        t.tag_configure("snip", font=("", 11), foreground=col("text"), **margins)
        t.tag_configure("warn", font=("", 11, "italic"), foreground=col("muted"), **margins)
        t.tag_configure("gap", font=("", 6), spacing3=6, **margins)
        t.tag_configure("more", font=("", 10, "bold"), foreground=col("focus"), **margins)
        t.tag_configure("xpage", font=("", 10, "bold"), foreground=col("muted"), lmargin1=nested, lmargin2=nested, rmargin=inset)
        t.tag_configure("xsnip", font=("", 11), foreground=col("text"), lmargin1=nested, lmargin2=nested, rmargin=inset)
        t.tag_configure("xmuted", font=("", 10, "italic"), foreground=col("muted"), **margins)
        t.tag_configure("hit", background=col("hit_bg"), foreground=col("hit_fg"))
        t.tag_raise("hit")
        self._repaint_badges()
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
        self._hide_tip()
        for name in [m for m in t.mark_names() if m.startswith(("ins", "bdg"))]:
            t.mark_unset(name)
        for i, segs in enumerate(blocks):
            start = t.index("end-1c")
            self._insert_badge(i)
            for text, tags in segs[:-1]:
                t.insert("end", text, tags)
            t.mark_set(f"ins{i}", "end-1c")      # qui, prima dello spazio finale, si inseriscono le «altre occorrenze»
            t.mark_gravity(f"ins{i}", "left")
            t.insert("end", *segs[-1])
            t.tag_add(f"res{i}", start, "end-1c")
        t.configure(state="disabled")
        t.yview_moveto(0)

    # ---- stato dei vettori (icona a inizio riga e suggerimento)
    def _kind(self, i: int) -> str | None:
        kind = (self.results[i].get("embedding") or {}).get("state")
        return kind if kind in ui_text.EMBEDDING_NAMES else None

    def _photo(self, kind: str) -> ImageTk.PhotoImage:
        key = (kind, dark())
        if key not in self._photos:
            self._photos[key] = ImageTk.PhotoImage(icon_pil("embedding_" + kind, key[1], self.BADGE_SIZE),
                                                   master=self.text)
        return self._photos[key]

    def _insert_badge(self, i: int):
        kind = self._kind(i)
        if kind:
            t = self.text
            t.mark_set(f"bdg{i}", "end-1c")
            t.mark_gravity(f"bdg{i}", "left")   # resta davanti all'immagine
            t.image_create("end", image=self._photo(kind), padx=3, align="center")
            t.tag_add("icon", f"bdg{i}", "end-1c")  # i margini della riga si leggono dal primo carattere

    def _repaint_badges(self):
        for i in range(len(self.results)):
            kind = self._kind(i)
            if kind and f"bdg{i}" in self.text.mark_names():
                try:
                    self.text.image_configure(f"bdg{i}", image=self._photo(kind))
                except tk.TclError:
                    pass

    def set_embedding(self, i: int, state: dict | None) -> bool:
        """Aggiorna lo stato del risultato `i` (per esempio a fine calcolo). True se è cambiato qualcosa."""
        if not 0 <= i < len(self.results) or self.results[i].get("embedding") == state:
            return False
        self.results[i]["embedding"] = state
        self._hide_tip()
        self._repaint_badges()
        return True

    def _hide_tip(self):
        self._tip_for = None
        self.tip.hide()

    def _motion(self, e):
        """Il suggerimento compare dopo una pausa sulla prima riga di un risultato; si sposta o sparisce con il cursore."""
        self._pointer = (e.x_root, e.y_root)
        tags = self.text.tag_names(f"@{e.x},{e.y}")
        i = self._index_at(e)
        if i is None or i >= len(self.results) or not ({"icon", "title"} & set(tags)) or not self._kind(i):
            self._hide_tip()
        elif i != self._tip_for:
            self.tip.hide()
            self._tip_for = i
            self.tip.schedule(ui_text.embedding_tip(self.results[i]["embedding"]),
                              lambda: (self._pointer[0], self._pointer[1], 1, 18))

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
            return [(tr("+ Altre occorrenze") + "\n", base + ("more",))]
        if state is None:
            return [(tr("Cerco le occorrenze…") + "\n", base + ("xmuted",))]
        items, total = state
        segs = [(tr("− Nascondi le occorrenze ({n})", n=ui_text.thousands(total)) + "\n", base + ("more",))]
        for k, it in enumerate(items):
            tags = base + ("occ", f"occ{k}")
            segs.append((it["location"] + " — " if it.get("location") else "", tags + ("xpage",)))
            segs += [(text, tags + ("xsnip",) + (("hit",) if tag == "hit" else ())) for text, tag in it["parts"]]
            segs.append(("\n", tags + ("xsnip",)))
        more = total - sum(it["count"] for it in items)
        if more > 0:
            segs.append((tr("… e altre {n}: nell'anteprima si vedono tutte", n=ui_text.thousands(more)) + "\n", base + ("xmuted",)))
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
        self._hide_tip()
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
