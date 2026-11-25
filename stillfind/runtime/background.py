# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Lavoro in background rispettoso: priorità bassa del thread e pausa quando il computer va a batteria."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

class PowerMonitor:
    """Priorità del lavoro in background e stato dell'alimentazione."""

    _cache: tuple[float, bool] = (0.0, False)

    @staticmethod
    def lower_thread_priority() -> None:
        """Abbassa la priorità del thread corrente: la lettura dei documenti non deve rallentare il resto.

        Windows: sotto la norma. macOS: priorità «background» del thread (limita anche l'uso del disco).
        Linux: nice 10 sul solo thread. Se il sistema non lo permette, si prosegue senza."""
        try:
            if sys.platform.startswith("win"):
                import ctypes
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
            elif sys.platform == "darwin":
                import ctypes
                ctypes.CDLL(None).setpriority(3, 0, 0x1000)  # PRIO_DARWIN_THREAD, thread corrente, PRIO_DARWIN_BG
            else:
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        except Exception:
            pass

    @staticmethod
    def _on_battery_now() -> bool:
        try:
            if sys.platform.startswith("win"):
                import ctypes

                class Status(ctypes.Structure):
                    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
                st = Status()
                ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st))  # type: ignore[attr-defined]
                return st.ACLineStatus == 0  # 1 = alimentatore collegato, 255 = sconosciuto
            if sys.platform == "darwin":
                out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
                return "Battery Power" in out
            base = "/sys/class/power_supply"
            kinds = {}
            for name in os.listdir(base):
                with open(f"{base}/{name}/type") as f:
                    kinds[name] = f.read().strip()
            if "Battery" not in kinds.values():
                return False  # computer fisso
            for name, kind in kinds.items():
                if kind == "Mains":
                    with open(f"{base}/{name}/online") as f:
                        if f.read().strip() == "1":
                            return False
            return True
        except Exception:
            return False  # nel dubbio si lavora

    @classmethod
    def on_battery(cls, max_age: float = 15.0) -> bool:
        """True se il computer è alimentato a batteria. Il risultato si riusa per `max_age` secondi."""
        now = time.monotonic()
        if now - cls._cache[0] > max_age or cls._cache[0] == 0.0:
            cls._cache = (now, cls._on_battery_now())
        return cls._cache[1]
