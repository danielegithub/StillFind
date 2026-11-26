# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Sblocco e migrazione prima di aprire connessioni o visualizzare documenti."""
import os
import hashlib
import queue
import sys
import threading
import weakref
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from stillfind.core import config
from stillfind.core.i18n import mark, tr
from stillfind.runtime.hotkey import Registration
from stillfind.runtime.signals import AppSignals
from stillfind.storage.database import Database
from stillfind.storage.protection import ArchiveProtection, Locked
from stillfind.ui.theme import PAL, font, ghost_button, primary_button
from stillfind.ui.widgets import SquareDialog, SquareEntry, ask_text, SquareRadioButton

MODES = {"plain": mark("In chiaro"), "auto": mark("Portachiavi dell’account"), "vault": mark("Cassaforte con passphrase")}


def restart(app, configure=False):
    app._close()
    ArchiveProtection.forget()
    cmd = AppSignals.command(*(["--configure-protection"] if configure else []))
    os.execv(sys.executable, cmd)


def startup(configure=False):
    path = config.DB_PATH
    ArchiveProtection.recover(path)
    spec = ArchiveProtection.settings(path)
    if spec["mode"] != "vault" and not configure:
        return True
    token = AppSignals.quit_token()
    ctk.set_appearance_mode(config.Settings.load().get("theme", "system"))
    root = ctk.CTk()
    root.withdraw()
    shortcut = Registration(lambda: config.Settings.load().get("global_shortcut", True),
                                   AppSignals.signal_window, lambda _: None)
    def tray_action(action):
        if action == "quit":
            AppSignals.quit_all()
        else:
            AppSignals.signal(action)
            AppSignals.signal_window()

    tray = Registration(lambda: True, tray_action, lambda _: None, tray=lambda: None)
    if sys.platform == "darwin":
        root.createcommand("::tk::mac::ReopenApplication", AppSignals.signal_window)

    def focus_dialog():
        nonlocal focus_job
        if token != AppSignals.quit_token():
            dialog = root.grab_current()
            if dialog and hasattr(dialog, "request_close"):
                dialog.request_close()
        elif AppSignals.consume("open"):
            dialog = root.grab_current()
            if dialog:
                dialog.lift()
                dialog.focus_force()
        focus_job = root.after(400, focus_dialog)
    focus_job = root.after(400, focus_dialog)
    try:
        if spec["mode"] == "vault":
            while True:
                phrase = ask_text(root, tr("Cassaforte StillFind"), tr("Passphrase per aprire l’archivio"),
                                  show="•", ok=tr("Apri"))
                if phrase is None:
                    return False
                try:
                    ArchiveProtection.unlock(path, phrase, hide_secret=config.Settings.load().get("hide_encryption_secret", False))
                    break
                except Locked as exc:
                    messagebox.showerror(tr("Cassaforte"),str(exc),parent=root)
                finally:
                    del phrase
        if configure and token == AppSignals.quit_token():
            wizard(root, spec)
        return True
    finally:
        root.after_cancel(focus_job)
        shortcut.close()
        tray.close()
        root.destroy()


def wizard(root, spec):
    win = ProtectionWizard(root, spec)
    root.wait_window(win)


class ProtectionWizard(SquareDialog):
    """Scelta della modalità di protezione dell'archivio e migrazione dei dati."""

    def __init__(self, root, spec):
        super().__init__(root, tr("Protezione dell’archivio"), resizable=False, modal=True)
        self.spec = spec
        self.busy = False
        pad = self.MARGIN
        self.ok = primary_button(self, tr("Applica protezione"), self.start, width=180)
        self.cancel_button = ghost_button(self, tr("Annulla"), self.close, width=100)
        self.button_bar(self.ok, self.cancel_button)
        body = ctk.CTkFrame(self, corner_radius=0, border_width=1, border_color=PAL["line"], fg_color=PAL["card"])
        body.pack(fill="both", expand=True, padx=pad, pady=(pad, 0))
        ctk.CTkLabel(body, text=tr("Protezione attuale · {mode}", mode=tr(MODES[spec["mode"]])), font=font(15, True),
                     text_color=PAL["text"], anchor="w").pack(anchor="w", padx=18, pady=(16, 8))
        self.mode = tk.StringVar(value=spec["mode"])
        for key, label in MODES.items():
            SquareRadioButton(body, text=tr(label), variable=self.mode, value=key, font=font(13),
                               radiobutton_width=20, radiobutton_height=20).pack(anchor="w", padx=18, pady=5)
        ctk.CTkLabel(body, text=tr("La modalità Portachiavi consente la lettura in background.\n"
                                   "La Cassaforte richiede la passphrase a ogni apertura."), font=font(12),
                     text_color=PAL["muted"], justify="left", anchor="w", wraplength=460
                     ).pack(anchor="w", padx=18, pady=(8, 12))
        ctk.CTkLabel(body, text=tr("Minuti di inattività prima del blocco della Cassaforte"), font=font(13),
                     text_color=PAL["text"], anchor="w").pack(anchor="w", padx=18)
        self.minutes = tk.StringVar(value=str(spec.get("minutes", 10)))
        SquareEntry(body, textvariable=self.minutes, width=90, height=34, font=font(13)
                    ).pack(anchor="w", padx=18, pady=(4, 12))
        self.note = ctk.CTkLabel(body, text=tr("La migrazione comprende testo, ricerche salvate e vettori."),
                                 font=font(12), text_color=PAL["muted"], justify="left", anchor="w", wraplength=460)
        self.note.pack(anchor="w", padx=18, pady=(0, 16))

    def close(self):
        if not self.busy:
            self.destroy()

    def start(self):
        spec, selected = self.spec, self.mode.get()
        phrase = None
        try:
            idle = int(self.minutes.get()) if selected == "vault" else 10
            if not 1 <= idle <= 1440:
                raise ValueError
        except ValueError:
            messagebox.showerror(tr("Cassaforte"), tr("Inserisci un tempo da 1 a 1440 minuti."), parent=self)
            return
        if selected == "vault":
            phrase = ask_text(self, tr("Cassaforte"), tr("Nuova passphrase"), show="•")
            if phrase is None:
                return
            repeat = ask_text(self, tr("Cassaforte"), tr("Ripeti la nuova passphrase"), show="•")
            if not phrase or repeat != phrase:
                messagebox.showerror(tr("Cassaforte"), tr("Le due passphrase devono coincidere."), parent=self)
                return
        if selected == "plain" and spec["mode"] != "plain":
            if not messagebox.askyesno(tr("Decifra archivio"), tr("Salvare testo, ricerche e vettori in chiaro su questo PC?"), parent=self):
                return
        if selected == spec["mode"] == "plain":
            self.destroy()
            return
        self.busy = True
        self.ok.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.note.configure(text=tr("Copia e verifica dell’archivio in corso…"))
        reply = queue.Queue()

        def work():
            try:
                Database.connect().close()
                ArchiveProtection.migrate(config.DB_PATH, selected, phrase, idle,
                                          hide_secret=config.Settings.load().get("hide_encryption_secret", False))
                reply.put(None)
            except Exception as exc:
                reply.put(str(exc))

        def poll():
            try:
                error = reply.get_nowait()
            except queue.Empty:
                self.after(100, poll)
                return
            self.busy = False
            if error:
                messagebox.showerror(tr("Protezione archivio"), error, parent=self)
                self.ok.configure(state="normal")
                self.cancel_button.configure(state="normal")
            else:
                self.destroy()
        threading.Thread(target=work, daemon=True).start()
        self.after(50, poll)


class SecretControls(ctk.CTkFrame):
    """Controlli condivisi fra Impostazioni e lucchetto; visibilità applicata subito."""

    _instances = weakref.WeakSet()

    @classmethod
    def clear_session_views(cls, app):
        """Rimuove subito anche le copie nei widget e negli appunti di questa finestra."""
        cls._clear_clipboard(app)
        for control in list(cls._instances):
            if control.app is app:
                control._clear_value()

    def __init__(self, master, app, *, show_value=False):
        super().__init__(master, corner_radius=0, border_width=1, border_color=PAL["line"], fg_color=PAL["card"])
        self.app = app
        self.show_value = show_value
        self.grid_columnconfigure(0, weight=1)
        self.title_label = ctk.CTkLabel(self, text=tr("Password e visibilità"), font=font(15, True), anchor="w")
        self.title_label.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        self.note = ctk.CTkLabel(self, text="", wraplength=600, justify="left", anchor="w", font=font(12), text_color=PAL["muted"])
        self.note.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.value = SquareEntry(self, height=36, border_width=1, font=font(12))
        if show_value:
            self.value.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.buttons = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.buttons.grid(row=3, column=0, sticky="w", padx=14, pady=(0, 12))
        self.visibility_button = ghost_button(self.buttons, "", self._toggle_visibility, width=250)
        self.visibility_button.pack(side="left")
        self.copy_button = ghost_button(self.buttons, tr("Copia"), self._copy, width=80)
        self.show_button = ghost_button(self.buttons, tr("Mostra password…"), lambda: SecretDialog(app, parent=self.winfo_toplevel()), width=160)
        if show_value:
            self.copy_button.pack(side="left", padx=(8, 0))
        else:
            self.show_button.pack(side="left", padx=(8, 0))
        self._instances.add(self)
        self.refresh()

    def _clear_value(self):
        self.value.configure(state="normal")
        self.value.delete(0, "end")
        self.value.configure(state="readonly")

    @staticmethod
    def _clear_clipboard(app):
        digest = getattr(app, "_secret_clipboard_digest", None)
        if digest:
            try:
                value = app.clipboard_get()
                if hashlib.sha256(value.encode("utf-8")).digest() == digest:
                    app.clipboard_clear()
            except tk.TclError:
                pass
            app._secret_clipboard_digest = None

    def refresh(self):
        self._clear_value()
        mode = ArchiveProtection.settings(config.DB_PATH)["mode"]
        hidden = bool(self.app.cfg.get("hide_encryption_secret", False))
        if hidden:
            self._clear_clipboard(self.app)
        if mode == "plain":
            self.note.configure(text=tr("L’archivio è in chiaro: non c’è una password da mostrare."))
            self.visibility_button.configure(text=tr("Password non disponibile"), state="disabled")
            self.copy_button.configure(state="disabled")
            self.show_button.configure(state="disabled")
            self.show_button.pack_forget()
            return
        noun = tr("Chiave del portachiavi") if mode == "auto" else tr("Passphrase della cassaforte")
        self.title_label.configure(text=noun)
        reveal_label = tr("Mostra password…") if self.show_value else tr("Riabilita visibilità…")
        self.visibility_button.configure(text=reveal_label if hidden else tr("Rendi invisibile la password…"), state="normal")
        self.show_button.configure(state="disabled" if hidden else "normal")
        if not self.show_value:
            if hidden:
                self.show_button.pack_forget()
            else:
                self.show_button.pack(side="left", padx=(8, 0))
        text = (tr("Password nascosta. Per mostrarla devi reinserire quella attuale.") if hidden else
                tr("Password visibile al clic sul lucchetto. Puoi nasconderla in qualsiasi momento."))
        if mode == "auto":
            text += " " + tr("La chiave è composta da 64 caratteri esadecimali.")
        self.note.configure(text=text + "\n" + tr("La visibilità si applica subito, anche se annulli le altre impostazioni."))
        self.copy_button.configure(state="disabled")
        if self.show_value:
            self.value.configure(state="normal")
            if hidden:
                self.value.insert(0, "••••••••••••")
            else:
                try:
                    secret = ArchiveProtection.visible_secret(config.DB_PATH)
                except Locked:
                    self.visibility_button.configure(text=tr("Mostra password…"))
                    self.note.configure(text=tr("Reinserisci la password attuale per mostrarla in questa sessione."))
                else:
                    try:
                        self.value.insert(0, secret.decode("utf-8"))
                        self.copy_button.configure(state="normal")
                    finally:
                        secret[:] = b"\0" * len(secret)
            self.value.configure(state="readonly")

    def _persist_visibility(self, hidden):
        # Salva solo questa preferenza immediata, senza applicare il resto del form.
        saved = config.Settings.load()
        saved["hide_encryption_secret"] = hidden
        saved.save()
        self.app.cfg["hide_encryption_secret"] = hidden

    def _toggle_visibility(self):
        hidden = bool(self.app.cfg.get("hide_encryption_secret", False))
        available = False
        try:
            secret = ArchiveProtection.visible_secret(config.DB_PATH)
        except Locked:
            pass
        else:
            available = True
            secret[:] = b"\0" * len(secret)
        if not hidden and available:
            if not messagebox.askyesno(tr("Nascondi password"), tr("Segna e conserva prima la password o la chiave.\n\n"
                                      "Una volta nascosta non sarà più visibile e dovrai reinserire quella attuale "
                                      "per mostrarla di nuovo. Vuoi nasconderla?"), parent=self.winfo_toplevel()):
                return
            try:
                self._persist_visibility(True)
            except OSError:
                messagebox.showerror(tr("Visibilità password"), tr("Non riesco a salvare la preferenza. Riprova."), parent=self.winfo_toplevel())
                return
            ArchiveProtection.hide_secret()
        else:
            phrase = ask_text(self.winfo_toplevel(), tr("Mostra password"),
                              tr("Reinserisci la passphrase attuale o la chiave del portachiavi (64 caratteri):"),
                              show="•", ok=tr("Mostra"))
            if phrase is None:
                return
            secret = bytearray(phrase.encode("utf-8"))
            del phrase
            try:
                ArchiveProtection.verify_secret(config.DB_PATH, secret)
                self._persist_visibility(False)
                ArchiveProtection.remember_secret(ArchiveProtection.settings(config.DB_PATH), secret)
            except (RuntimeError, OSError) as exc:
                messagebox.showerror(tr("Visibilità password"), str(exc), parent=self.winfo_toplevel())
                return
            finally:
                secret[:] = b"\0" * len(secret)
        for control in list(self._instances):
            control.refresh()

    def _copy(self):
        if self.app.cfg.get("hide_encryption_secret", False):
            return
        secret = ArchiveProtection.visible_secret(config.DB_PATH)
        try:
            self.clipboard_clear()
            self.clipboard_append(secret.decode("utf-8"))
            self.app._secret_clipboard_digest = hashlib.sha256(secret).digest()
        finally:
            secret[:] = b"\0" * len(secret)

    def destroy(self):
        self._instances.discard(self)
        self._clear_value()
        super().destroy()


class SecretDialog(SquareDialog):
    """Pannello locale del lucchetto, senza segreti nella configurazione."""

    def __init__(self, app, parent=None):
        parent = parent or app
        super().__init__(parent, tr("Protezione dell’archivio"), "720x360", minsize=(680, 340), modal=True)
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        if ArchiveProtection.settings(config.DB_PATH)["mode"] == "plain":
            panel = ctk.CTkFrame(self, corner_radius=0, border_width=1, border_color=PAL["line"], fg_color=PAL["card"])
            panel.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
            ctk.CTkLabel(panel, text=tr("Archivio NON CIFRATO"), font=font(18, True)).pack(anchor="w", padx=16, pady=(18, 8))
            ctk.CTkLabel(panel, text=tr("Testo, ricerche salvate e vettori sono in chiaro sul disco.\n"
                            "Puoi proteggerli con il portachiavi del computer o una passphrase."),
                         font=font(13), justify="left", wraplength=620).pack(anchor="w", padx=16)
            self.configure_button = primary_button(panel, tr("Attiva cifratura…"), lambda: restart(app, True), width=180)
            self.configure_button.pack(anchor="w", padx=16, pady=18)
        else:
            self.controls = SecretControls(self, app, show_value=True)
            self.controls.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        ghost_button(self, tr("Chiudi"), self.close, width=100).grid(row=1, column=0, sticky="e", padx=16, pady=(0, 16))
