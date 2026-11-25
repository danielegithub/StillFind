# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Sblocco e migrazione prima di aprire connessioni o visualizzare documenti."""
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from stillfind.core import config
from stillfind.runtime.hotkey import Registration
from stillfind.runtime.signals import AppSignals
from stillfind.storage.database import Database
from stillfind.storage.protection import ArchiveProtection, Locked

MODES = {"plain": "In chiaro", "auto": "Portachiavi dell’account", "vault": "Cassaforte con passphrase"}


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
    root = tk.Tk()
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
            if isinstance(dialog, simpledialog.Dialog):
                dialog.cancel()
            elif dialog and hasattr(dialog, "request_close"):
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
                phrase = simpledialog.askstring("Cassaforte StillFind", "Passphrase per aprire l’archivio",
                                               show="•",parent=root)
                if phrase is None:
                    return False
                try:
                    ArchiveProtection.unlock(path,phrase)
                    break
                except Locked as exc:
                    messagebox.showerror("Cassaforte",str(exc),parent=root)
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
    win = tk.Toplevel(root)
    win.title("Protezione dell’archivio")
    body = ttk.Frame(win,padding=16)
    body.pack(fill="both",expand=True)
    ttk.Label(body,text="Protezione attuale · "+MODES[spec["mode"]]).pack(anchor="w")
    mode = tk.StringVar(value=spec["mode"])
    for key,label in MODES.items():
        ttk.Radiobutton(body,text=label,variable=mode,value=key).pack(anchor="w",pady=3)
    ttk.Label(body,text="La modalità Portachiavi consente la lettura in background.\\n"
              "La Cassaforte richiede la passphrase a ogni apertura.").pack(anchor="w",pady=6)
    minutes = tk.StringVar(value=str(spec.get("minutes",10)))
    ttk.Label(body,text="Minuti di inattività prima del blocco della Cassaforte").pack(anchor="w")
    ttk.Entry(body,textvariable=minutes,width=8).pack(anchor="w")
    note = ttk.Label(body,text="La migrazione comprende testo, ricerche salvate e vettori.")
    note.pack(anchor="w",pady=8)
    bar = ttk.Frame(body)
    bar.pack(fill="x")
    cancel = ttk.Button(bar,text="Annulla",command=win.destroy)
    cancel.pack(side="right")
    def start():
        selected = mode.get()
        phrase = None
        try:
            idle = int(minutes.get()) if selected == "vault" else 10
            if not 1 <= idle <= 1440:
                raise ValueError
        except ValueError:
            messagebox.showerror("Cassaforte","Inserisci un tempo da 1 a 1440 minuti.",parent=win)
            return
        if selected == "vault":
            phrase = simpledialog.askstring("Cassaforte","Nuova passphrase",show="•",parent=win)
            if phrase is None:
                return
            repeat = simpledialog.askstring("Cassaforte","Ripeti la nuova passphrase",show="•",parent=win)
            if not phrase or repeat != phrase:
                messagebox.showerror("Cassaforte","Le due passphrase devono coincidere.",parent=win)
                return
        if selected == "plain" and spec["mode"] != "plain":
            if not messagebox.askyesno("Decifra archivio","Salvare testo, ricerche e vettori in chiaro su questo PC?",parent=win):
                return
        if selected == spec["mode"] == "plain":
            win.destroy()
            return
        ok.configure(state="disabled")
        cancel.configure(state="disabled")
        win.protocol("WM_DELETE_WINDOW",lambda: None)
        note.configure(text="Copia e verifica dell’archivio in corso…")
        reply = queue.Queue()
        def work():
            try:
                Database.connect().close()
                ArchiveProtection.migrate(config.DB_PATH,selected,phrase,idle)
                reply.put(None)
            except Exception as exc:
                reply.put(str(exc))
        threading.Thread(target=work,daemon=True).start()
        def poll():
            try:
                error = reply.get_nowait()
            except queue.Empty:
                win.after(100,poll)
                return
            if error:
                messagebox.showerror("Protezione archivio",error,parent=win)
                ok.configure(state="normal")
                cancel.configure(state="normal")
                win.protocol("WM_DELETE_WINDOW",win.destroy)
            else:
                win.destroy()
        win.after(50,poll)
    ok = ttk.Button(bar,text="Applica protezione",command=start)
    ok.pack(side="right",padx=6)
    win.request_close = lambda: win.destroy() if not ok.instate(["disabled"]) else None
    win.grab_set()
    root.wait_window(win)
