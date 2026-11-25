# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Segnali tra i processi di StillFind (finestra, agente, icona di sistema, scorciatoia)."""
import ctypes as C
import subprocess
import sys

from stillfind.core.config import APP_DIR


class AppSignals:
    """Comunicazione tra processi tramite piccoli file nella cartella dei dati."""

    LABEL = "⌘+⌥+K" if sys.platform == "darwin" else "Ctrl+Alt+K"
    WINDOW_ACTIONS = ("open", "settings", "about", "pause", "resume")
    TRAY_ACTIONS = (*WINDOW_ACTIONS, "quit")

    @staticmethod
    def command(*args):
        """Comando per riavviare StillFind (anche dall'eseguibile installato) con gli argomenti dati."""
        return [sys.executable, *([] if getattr(sys, "frozen", False) else ["-m", "stillfind"]), *args]

    @staticmethod
    def _path(action):
        return APP_DIR / "apri-ricerca" if action == "open" else APP_DIR / ("comando-" + action)

    @classmethod
    def signal(cls, action):
        cls._path(action).touch(mode=0o600)

    @classmethod
    def consume(cls, action):
        try:
            cls._path(action).unlink()
            return True
        except FileNotFoundError:
            return False

    @classmethod
    def quit_token(cls):
        try:
            return cls._path("quit").stat().st_mtime_ns
        except FileNotFoundError:
            return 0

    @classmethod
    def quit_all(cls):
        cls.signal("quit")

    @staticmethod
    def allow_window_focus():
        # Windows consente al processo che ha ricevuto il clic di cedere il primo piano.
        if sys.platform == "win32":
            try:
                pid = int((APP_DIR / "window.pid").read_text("ascii"))
                C.windll.user32.AllowSetForegroundWindow(pid)
            except (OSError, ValueError):
                pass

    @classmethod
    def signal_window(cls):
        cls.allow_window_focus()
        cls.signal("open")

    @staticmethod
    def window_owns_tray():
        from stillfind.core.locks import ProcessLock
        lock = ProcessLock.acquire(APP_DIR / "window-tray.lock")
        if lock is None:
            return True
        lock.close()
        return False

    @classmethod
    def show(cls, action="open"):
        subprocess.Popen(cls.command("--window-action", action, "--quit-token", str(cls.quit_token())),
                         stdin=subprocess.DEVNULL)
