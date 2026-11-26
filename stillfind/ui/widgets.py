# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Widget riutilizzabili."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from contextlib import closing
from datetime import date
from tkinter import ttk

import customtkinter as ctk

from stillfind.core import config, labels as ui_text
from stillfind.core import i18n
from stillfind.core.i18n import tr
from stillfind.storage.protection import ArchiveProtection
from stillfind.ui.theme import PAL, action_icon, col, font, ghost_button, primary_button


class SquareToggle(ctk.CTkSwitch):
    """Selettore on/off rettangolare, utilizzabile anche con Tab, Spazio e Invio."""

    # CTk normalmente sposta il focus sull'etichetta interna, che non ha i nostri binding.
    focus = tk.Misc.focus
    focus_set = tk.Misc.focus_set
    focus_force = tk.Misc.focus_force

    def __init__(self, master, **kwargs):
        kwargs.update(corner_radius=0, border_width=1, button_length=14)
        kwargs.setdefault("switch_width", 40)
        kwargs.setdefault("switch_height", 20)
        kwargs.setdefault("border_color", PAL["line"])
        super().__init__(master, **kwargs)
        tk.Frame.configure(self, takefocus=True)
        tk.Misc.bind(self, "<FocusIn>", lambda event: self.configure(border_color=PAL["focus"]))
        tk.Misc.bind(self, "<FocusOut>", lambda event: self.configure(border_color=PAL["line"]))
        for event in ("<space>", "<Return>"):
            tk.Misc.bind(self, event, self._press)

    def _press(self, event=None):
        self.toggle()
        return "break"

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        # Con raggio zero CTk estende il cursore fino al bordo del binario.
        # Un contorno separato mantiene leggibile anche il cursore bianco sul pannello.
        self._canvas.delete("square_outline")
        scale = self._apply_widget_scaling
        self._canvas.create_rectangle(scale(0.5), scale(0.5), scale(self._switch_width - 0.5),
                                      scale(self._switch_height - 0.5), width=scale(1),
                                      outline=self._apply_appearance_mode(self._border_color),
                                      tags="square_outline")


class SquareSelector(ctk.CTkSegmentedButton):
    """Selettore a segmenti con spigoli vivi e divisori di un pixel."""

    def __init__(self, master, **kwargs):
        kwargs.update(corner_radius=0, border_width=1)
        super().__init__(master, **kwargs)


TEXT_GAP = 10  # spazio fra una casella (o un pulsante di scelta) e il suo testo


class SquareCheckBox(ctk.CTkCheckBox):
    """Casella di spunta con un po' d'aria fra il quadratino e il testo."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._text_label.grid_configure(padx=(TEXT_GAP, 0))


class SquareRadioButton(ctk.CTkRadioButton):
    """Pulsante di scelta con un po' d'aria fra il cerchietto e il testo."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._text_label.grid_configure(padx=(TEXT_GAP, 0))


class IdSelector(SquareSelector):
    """Selettore a segmenti con identificatori interni: mostra testi tradotti e restituisce l'identificatore."""

    def __init__(self, master, items, command=None, **kwargs):
        self._ids = [key for key, _ in items]
        self._labels = [label for _, label in items]
        self._user_command = command
        super().__init__(master, values=self._labels, command=self._chosen, **kwargs)

    def _chosen(self, label):
        if self._user_command:
            self._user_command(self._ids[self._labels.index(label)])

    def get(self):
        label = super().get()
        return self._ids[self._labels.index(label)] if label in self._labels else ""

    def set(self, key, from_variable_callback=False, from_button_callback=False):
        # CTk richiama set() con il testo del pulsante premuto; dal codice dell'app si passa l'identificatore.
        label = key if from_button_callback or key not in self._ids else self._labels[self._ids.index(key)]
        super().set(label, from_variable_callback, from_button_callback)

    def button(self, key):
        return self._buttons_dict[self._labels[self._ids.index(key)]]


TEXT_INSET = 10  # margine interno del testo nei campi, per non farlo toccare il bordo


class SquareEntry(ctk.CTkEntry):
    """Campo di testo a spigolo vivo con margine interno leggibile."""

    def __init__(self, master, **kwargs):
        kwargs.update(corner_radius=0)
        super().__init__(master, **kwargs)

    def _create_grid(self):
        super()._create_grid()
        scale, border = self._apply_widget_scaling, self._border_width
        self._entry.grid_configure(padx=scale(TEXT_INSET), pady=(scale(border), scale(border)))


class _SquareDropdown:
    """Geometria dei menu CTk senza la freccia nativa a estremità arrotondate."""

    def __init__(self, master, **kwargs):
        kwargs["corner_radius"] = 0
        super().__init__(master, **kwargs)

    def _create_grid(self):
        super()._create_grid()
        field = getattr(self, "_text_label", None) or self._entry
        right = field.grid_info()["padx"]
        # Con raggio zero CTk lascia solo 3 px a sinistra: il testo toccherebbe il bordo.
        field.grid_configure(padx=(self._apply_widget_scaling(TEXT_INSET), right[1] if isinstance(right, tuple) else right))

    def _draw(self, no_color_updates=False):
        self._canvas.delete("dropdown_arrow")
        super()._draw(no_color_updates)
        color = self._canvas.itemcget("dropdown_arrow", "fill")
        self._canvas.delete("dropdown_arrow")
        scale = self._apply_widget_scaling
        x, y = self._current_width - self._current_height / 2, self._current_height / 2
        points = ((-5, -3), (5, -3), (5, -1), (3, -1), (3, 1), (1, 1),
                  (1, 3), (-1, 3), (-1, 1), (-3, 1), (-3, -1), (-5, -1))
        coords = [coordinate for dx, dy in points for coordinate in (scale(x + dx), scale(y + dy))]
        self._canvas.create_polygon(coords, fill=color, outline="", tags="dropdown_arrow")
        self._canvas.delete("square_outline")
        self._canvas.create_rectangle(scale(0.5), scale(0.5), scale(self._current_width - 0.5),
                                      scale(self._current_height - 0.5), width=scale(1),
                                      outline=self._apply_appearance_mode(PAL["line"]), tags="square_outline")


class SquareOptionMenu(_SquareDropdown, ctk.CTkOptionMenu):
    """Menu a scelta rettangolare con bordo di un pixel."""


class SquareComboBox(_SquareDropdown, ctk.CTkComboBox):
    """Campo e menu a discesa con bordo di un pixel e freccia ortogonale."""


class _MenuButton(ctk.CTkButton):
    # CTk sposterebbe il focus sull'etichetta interna, che non ha i nostri binding.
    focus = tk.Misc.focus
    focus_set = tk.Misc.focus_set
    focus_force = tk.Misc.focus_force


class MenuBar(ctk.CTkFrame):
    """Barra dei menu classica a spigolo vivo: ogni voce apre un menu a tendina sotto di sé.

    I menu sono `tk.Menu` (nativi su macOS, colorati e piatti altrove); la barra
    riprende palette e tema dell'app e si usa anche con Tab, Spazio, Invio e freccia giù."""

    def __init__(self, master):
        super().__init__(master, corner_radius=0, fg_color=PAL["card"], border_width=0)
        self.menus: dict[str, tk.Menu] = {}
        self.buttons: dict[str, ctk.CTkButton] = {}

    def add(self, title, fill, before_open=None):
        """`fill(menu)` aggiunge le voci; `before_open(menu)` aggiorna etichette e abilitazione a ogni apertura."""
        menu = tk.Menu(self, tearoff=0)
        fill(menu)
        button = _MenuButton(self, text=title, width=0, height=30, corner_radius=0, border_width=1,
                               border_color=PAL["card"], fg_color="transparent", hover_color=PAL["ghost_hover"],
                               text_color=PAL["text"], font=font(13), border_spacing=0,
                               command=lambda: self.open(title))
        button.configure(width=len(title) * 8 + 28)
        button.grid(row=0, column=len(self.menus), padx=(10 if not self.menus else 0, 2), pady=4)
        tk.Frame.configure(button, takefocus=True)
        tk.Misc.bind(button, "<FocusIn>", lambda e: button.configure(border_color=PAL["focus"]))
        tk.Misc.bind(button, "<FocusOut>", lambda e: button.configure(border_color=PAL["card"]))
        for key in ("<Return>", "<space>", "<Down>"):
            tk.Misc.bind(button, key, lambda e, t=title: (self.open(t), "break")[1])
        self.menus[title], self.buttons[title] = menu, button
        self._before_open = getattr(self, "_before_open", {})
        self._before_open[title] = before_open
        self.restyle()
        return menu

    def open(self, title):
        button, menu = self.buttons[title], self.menus[title]
        if self._before_open.get(title):
            self._before_open[title](menu)
        menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        menu.grab_release()

    def restyle(self):
        for menu in self.menus.values():
            menu.configure(background=col("card"), foreground=col("text"), activebackground=col("sel"),
                           activeforeground=col("text"), disabledforeground=col("muted"),
                           borderwidth=1, activeborderwidth=0, relief="solid")


class PreviewAction(ctk.CTkButton):
    focus = tk.Misc.focus
    focus_set = tk.Misc.focus_set
    focus_force = tk.Misc.focus_force

    def __init__(self, master, action, label, command):
        primary = action == "open"
        self._tip_text = label
        self._tip = self._tip_job = None

        def activate():
            self._hide_tip()
            command()

        super().__init__(master, text="", image=action_icon(action), command=activate,
                         width=38, height=36, corner_radius=0, border_width=1, border_spacing=0,
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
                             padx=8, pady=4, takefocus=False, borderwidth=0,
                             highlightthickness=1, highlightbackground=col("line"))
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


class HoverTip:
    """Suggerimento a comparsa per un widget Tk: appare dopo una breve pausa, vicino al punto indicato
    dall'ancora, e sparisce con `hide()`. Non ruba il focus e non sopravvive al widget."""

    DELAY = 450
    WRAP = 380

    def __init__(self, owner):
        self.owner = owner
        self.label = self.job = None

    @property
    def shown(self) -> bool:
        return self.label is not None

    def schedule(self, text, anchor, delay=None):
        """`anchor()` restituisce (x, y, larghezza, altezza) in coordinate dello schermo, calcolate al momento."""
        self.hide()
        self.job = self.owner.after(self.DELAY if delay is None else delay, lambda: self.show(text, anchor))

    def show(self, text, anchor):
        self.hide()
        try:
            top = self.owner.winfo_toplevel()
            label = tk.Label(top, text=text, bg=col("text"), fg=col("card"), padx=8, pady=5, takefocus=False,
                             borderwidth=0, highlightthickness=1, highlightbackground=col("line"),
                             justify="left", wraplength=self.WRAP, font=("", 12))
            ax, ay, aw, ah = anchor()
            x, y = ax - top.winfo_rootx(), ay - top.winfo_rooty()
            below = y + ah + 6
            height = label.winfo_reqheight()
            label.place(x=max(0, min(x, top.winfo_width() - label.winfo_reqwidth())),
                        y=below if below + height <= top.winfo_height() else max(0, y - height - 6))
            label.lift()
        except tk.TclError:
            return
        self.label = label

    def hide(self):
        try:
            if self.job is not None:
                self.owner.after_cancel(self.job)
        except tk.TclError:
            pass
        self.job = None
        if self.label is not None:
            try:
                self.label.destroy()
            except tk.TclError:
                pass
            self.label = None


class EmbeddingBadge(ctk.CTkLabel):
    """Icona dello stato dei vettori di un documento, con il suo suggerimento (anche da tastiera con Tab)."""

    focus = tk.Misc.focus
    focus_set = tk.Misc.focus_set
    focus_force = tk.Misc.focus_force

    def __init__(self, master):
        super().__init__(master, text="", width=26, height=26)
        self.embedding: dict | None = None
        self.tip = HoverTip(self)
        tk.Frame.configure(self, takefocus=True)
        self.bind("<Enter>", self._schedule)
        self.bind("<Leave>", lambda e: self.tip.hide())
        for event in ("<FocusOut>", "<Escape>", "<Unmap>", "<Button-1>"):
            tk.Misc.bind(self, event, lambda e: self.tip.hide())
        tk.Misc.bind(self, "<FocusIn>", lambda e: self.tip.show(self.tip_text(), self.tip_anchor))

    def tip_text(self) -> str:
        return ui_text.embedding_tip(self.embedding)

    def tip_anchor(self):
        return self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height()

    def _schedule(self, event=None):
        if self.embedding and not self.tip.shown and self.tip.job is None:
            self.tip.schedule(self.tip_text(), self.tip_anchor)

    def set_state(self, state: dict | None):
        """Mostra l'icona dello stato; senza stato (documento non più nell'archivio) il badge si nasconde."""
        if state == self.embedding and self.winfo_ismapped() == bool(state):
            return  # nessun cambiamento (aggiornamento periodico): il suggerimento aperto resta dov'è
        self.embedding = state
        self.tip.hide()
        if state and state.get("state") in ui_text.EMBEDDING_NAMES:
            self.configure(image=action_icon("embedding_" + state["state"]))
            self.grid()
        else:
            self.grid_remove()

    def destroy(self):
        self.tip.hide()
        super().destroy()


class FooterIndicator(ctk.CTkLabel):
    """Riga di testo del piè di pagina con suggerimento a comparsa (anche da tastiera con Tab)."""

    focus = tk.Misc.focus
    focus_set = tk.Misc.focus_set
    focus_force = tk.Misc.focus_force

    def __init__(self, master):
        super().__init__(master, text="", anchor="w", height=18, font=font(12))
        self.tip_text = ""
        self.tip = HoverTip(self)
        tk.Frame.configure(self, takefocus=True)
        self.bind("<Enter>", self._schedule)
        self.bind("<Leave>", lambda e: self.tip.hide())
        for event in ("<FocusOut>", "<Escape>", "<Unmap>", "<Button-1>"):
            tk.Misc.bind(self, event, lambda e: self.tip.hide())
        tk.Misc.bind(self, "<FocusIn>", lambda e: self.tip.show(self.tip_text, self.tip_anchor))

    def tip_anchor(self):
        return self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height()

    def _schedule(self, event=None):
        if self.tip_text and not self.tip.shown and self.tip.job is None:
            self.tip.schedule(self.tip_text, self.tip_anchor)

    def set_state(self, text: str, tone: str, tip: str):
        """Aggiorna testo, colore e suggerimento; il suggerimento aperto cambia subito se il contenuto è diverso."""
        changed = tip != self.tip_text
        self.tip_text = tip
        self.configure(text=text, text_color=PAL[tone])
        if changed and self.tip.shown:
            self.tip.show(tip, self.tip_anchor)

    def destroy(self):
        self.tip.hide()
        super().destroy()


class ModelsPanel(ctk.CTkFrame):
    """Riquadro fisso a destra del piè di pagina: modello del significato e OCR, con divisori netti."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent", border_width=0)
        self.divider = ctk.CTkFrame(self, width=2, height=1, corner_radius=0, fg_color=PAL["muted"], border_width=0)
        self.divider.grid(row=0, column=0, rowspan=3, sticky="ns", padx=(0, 12))
        self.semantic = FooterIndicator(self)
        self.semantic.grid(row=0, column=1, sticky="w")
        self.rule = ctk.CTkFrame(self, height=1, corner_radius=0, fg_color=PAL["line"], border_width=0)
        self.rule.grid(row=1, column=1, sticky="ew", pady=2)
        self.ocr = FooterIndicator(self)
        self.ocr.grid(row=2, column=1, sticky="w")

    def set_state(self, labels: dict):
        self.semantic.set_state(**labels["semantic"])
        self.ocr.set_state(**labels["ocr"])


class SquareDialog(ctk.CTkToplevel):
    """Base delle finestre secondarie: sfondo, margini e chiusura con Esc uguali ovunque.

    `size` è «LxA»; senza, la finestra prende le dimensioni del contenuto.
    Con `modal` blocca la finestra madre dopo la comparsa (su alcune piattaforme il blocco
    richiede che la finestra sia già visibile)."""

    MARGIN = 16

    def __init__(self, parent, title, size=None, *, minsize=None, resizable=True, modal=False):
        self._previous_grab = parent.grab_current() if modal else None
        super().__init__(parent)
        self.title(title)
        if size:
            self.geometry(size)
        if minsize:
            self.minsize(*minsize)
        if not resizable:
            self.resizable(False, False)
        self.configure(fg_color=PAL["bg"])
        # Una finestra madre nascosta (sblocco all'avvio) renderebbe invisibile anche questa.
        if parent.winfo_viewable():
            self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda event: self.close())
        if modal:
            self.after(150, self._grab)

    def _grab(self):
        try:
            self.lift()
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass  # finestra già chiusa

    def close(self):
        self.destroy()

    def destroy(self):
        previous = self._previous_grab
        super().destroy()
        # Una finestra modale aperta sopra un'altra le restituisce il blocco.
        if previous is not None:
            try:
                if previous.winfo_exists():
                    previous.grab_set()
            except tk.TclError:
                pass

    def request_close(self):
        """Chiusura richiesta dall'esterno (per esempio quando l'app viene chiusa da un'altra istanza)."""
        self.close()

    def button_bar(self, *buttons):
        """Barra dei pulsanti in basso a destra; il primo è quello principale (all'estrema destra)."""
        bar = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, border_width=0)
        bar.pack(side="bottom", fill="x", padx=self.MARGIN, pady=self.MARGIN)
        for i, button in enumerate(buttons):
            button.pack(in_=bar, side="right", padx=(0, 8 if i else 0))
            button.lift()  # creato prima della barra: senza, resterebbe nascosto sotto di essa
        return bar


class TextPrompt(SquareDialog):
    """Richiesta di una riga di testo (anche mascherata): sostituisce `simpledialog.askstring`."""

    def __init__(self, parent, title, prompt, show="", ok="OK"):
        super().__init__(parent, title, resizable=False, modal=True)
        self.result = None
        ctk.CTkLabel(self, text=prompt, font=font(13), text_color=PAL["text"], wraplength=420,
                     justify="left", anchor="w").pack(anchor="w", padx=self.MARGIN, pady=(self.MARGIN, 8))
        self.entry = SquareEntry(self, width=420, height=36, font=font(13), show=show)
        self.entry.pack(padx=self.MARGIN, pady=(0, 4))
        self.entry.bind("<Return>", lambda event: self._accept())
        self.button_bar(primary_button(self, ok, self._accept, width=100), ghost_button(self, tr("Annulla"), self.close, width=100))
        self.after(200, self._focus)

    def _focus(self):
        try:
            self.entry.focus_set()
        except tk.TclError:
            pass

    def _accept(self):
        self.result = self.entry.get()
        self.destroy()


def ask_text(parent, title, prompt, show="", ok="OK"):
    """Testo inserito, oppure None se l'utente annulla."""
    dialog = TextPrompt(parent, title, prompt, show=show, ok=ok)
    dialog.wait_window()
    return dialog.result


class TreeFrame(ctk.CTkFrame):
    """Elenco ad albero in un riquadro a spigolo vivo, con barra di scorrimento dell'app.

    `columns` è una lista di (chiave, titolo, larghezza). Il `ttk.Treeview` è in `.tree`."""

    def __init__(self, master, columns=(), show="headings", **tree_options):
        super().__init__(master, corner_radius=0, fg_color=PAL["card"], border_width=1, border_color=PAL["line"])
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show=show, selectmode="browse",
                                 style="Cerca.Treeview", **tree_options)
        for key, title, width in columns:
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=width, minwidth=40, anchor="w", stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=6)
        scroll = ctk.CTkScrollbar(self, command=self.tree.yview, corner_radius=0, width=12)
        scroll.grid(row=0, column=1, sticky="ns", padx=2, pady=2)
        self.tree.configure(yscrollcommand=scroll.set)


# --------------------------------------------------------------------------


def read_in_background(widget, work, display):
    # Tk rimane sul thread principale. Le finestre chiuse e le selezioni superate scartano la risposta.
    ticket = object()
    widget._request = ticket
    reply = queue.SimpleQueue()

    def worker():
        try:
            with closing(ArchiveProtection.connection(config.DB_PATH)) as con:
                con.execute("BEGIN")
                reply.put((work(con), None))
        except Exception as exc:
            reply.put((None, str(exc)))

    def poll():
        if widget._request is not ticket or not widget.winfo_exists():
            return
        try:
            data, error = reply.get_nowait()
        except queue.Empty:
            widget.after(50, poll)
        else:
            display(data, error)

    threading.Thread(target=worker, daemon=True).start()
    widget.after(50, poll)


def make_tree(parent, columns, branch_title=None, framed=False):
    """Albero con le colonne date. `framed` lo racchiude in un riquadro con bordo (finestre secondarie)."""
    if framed:
        frame = TreeFrame(parent, columns, show="tree headings" if branch_title else "headings")
        frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        tree = frame.tree
        if branch_title:
            tree.heading("#0", text=branch_title, anchor="w")
            tree.column("#0", width=205, minwidth=100)
        return tree
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    frame.pack(fill="both", expand=True, padx=6, pady=4)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="tree headings" if branch_title else "headings", selectmode="browse",
                        style="Cerca.Treeview")
    if branch_title:
        tree.heading("#0", text=branch_title)
        tree.column("#0", width=205, minwidth=100)
    for key, title, width in columns:
        tree.heading(key, text=title)
        tree.column(key, width=width, minwidth=40, stretch=True)
    scroll = ttk.Scrollbar(frame, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    tree.pack(fill="both", expand=True)
    return tree


def short_date(iso):
    if not iso:
        return tr("Data sconosciuta")
    day = date.fromisoformat(iso)
    return i18n.Formats.numeric_date(day.year, day.month, day.day)
