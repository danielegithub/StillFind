# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Avvio di StillFind all'accesso al computer."""
from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from stillfind.runtime.signals import AppSignals


class Autostart:
    """All'accesso parte l'agente in silenzio (`--agent`): scorciatoia globale, icona di sistema e controllo delle
    cartelle. Non servono permessi di amministratore e la voce si toglie con lo stesso interruttore."""

    MAC_LABEL = "app.stillfind.agent"
    WINDOWS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    WINDOWS_VALUE = "StillFind"

    @staticmethod
    def command() -> list[str]:
        return AppSignals.command("--agent")

    @classmethod
    def _mac_file(cls) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{cls.MAC_LABEL}.plist"

    @staticmethod
    def _linux_file() -> Path:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        return config / "autostart" / "stillfind.desktop"

    @classmethod
    def is_enabled(cls) -> bool:
        if sys.platform == "darwin":
            return cls._mac_file().is_file()
        if sys.platform.startswith("win"):
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.WINDOWS_KEY) as key:
                    winreg.QueryValueEx(key, cls.WINDOWS_VALUE)
                return True
            except OSError:
                return False
        return cls._linux_file().is_file()

    @classmethod
    def enable(cls) -> None:
        command = cls.command()
        if sys.platform == "darwin":
            path = cls._mac_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(plistlib.dumps({"Label": cls.MAC_LABEL, "ProgramArguments": command, "RunAtLoad": True,
                                             "ProcessType": "Background"}))
        elif sys.platform.startswith("win"):
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cls.WINDOWS_KEY) as key:
                winreg.SetValueEx(key, cls.WINDOWS_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
        else:
            path = cls._linux_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[Desktop Entry]\nType=Application\nName=StillFind\nComment=StillFind\n"
                            f"Exec={' '.join(shlex.quote(part) for part in command)}\nTerminal=false\n"
                            "X-GNOME-Autostart-enabled=true\n", encoding="utf-8")

    @classmethod
    def disable(cls) -> None:
        if sys.platform == "darwin":
            cls._mac_file().unlink(missing_ok=True)
        elif sys.platform.startswith("win"):
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.WINDOWS_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, cls.WINDOWS_VALUE)
            except OSError:
                pass
        else:
            cls._linux_file().unlink(missing_ok=True)

    @classmethod
    def apply(cls, wanted: bool) -> None:
        cls.enable() if wanted else cls.disable()
