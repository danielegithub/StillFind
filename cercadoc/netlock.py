# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Isolamento dalla rete a livello di sistema operativo, per il processo che esegue i modelli.

Il blocco dei moduli Python (`offline.py`) non ferma il codice nativo (Tesseract, onnxruntime, numpy). Qui il
processo lavoratore viene avviato dentro un recinto che **toglie la rete al kernel**, qualunque codice giri:

  macOS    `sandbox-exec` con un profilo che nega ogni operazione di rete (incluso locale);
  Linux    `unshare --user --net` (o `bwrap --unshare-net`): il processo vive in una rete vuota, senza interfacce;
  Windows  nessun meccanismo per singolo processo senza permessi di amministratore: vale la regola del
           firewall creata dall'installer (sezione 14).

È una difesa in più, non l'unica: restano il blocco dei moduli Python, il controllo del modello e la verifica
con `lsof`. Se il recinto non è disponibile o non parte, il lavoratore si avvia comunque e la cosa viene segnalata."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

PROFILE = "(version 1)(allow default)(deny network*)"

# prova eseguita *dentro* il recinto: senza recinto la porta locale rifiuta la connessione (nessuno ascolta),
# con il recinto il sistema impedisce proprio di tentarla (permesso negato, rete irraggiungibile…)
_PROBE = ("import socket\n"
          "try:\n"
          "    socket.create_connection(('127.0.0.1', 9), timeout=3)\n"
          "    print('APERTA')\n"
          "except ConnectionRefusedError:\n"
          "    print('APERTA')\n"
          "except OSError as e:\n"
          "    print('BLOCCATA', e.errno)\n")


def wrapper() -> tuple[list[str], str] | None:
    """Prefisso del comando che avvia un processo senza rete, e il nome del meccanismo; None se non c'è."""
    if sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec"):
        return ["/usr/bin/sandbox-exec", "-p", PROFILE], "sandbox-exec"
    if sys.platform.startswith("linux"):
        unshare = shutil.which("unshare")
        if unshare:
            return [unshare, "--user", "--map-root-user", "--net", "--"], "unshare"
        bwrap = shutil.which("bwrap")
        if bwrap:
            return [bwrap, "--unshare-net", "--dev-bind", "/", "/"], "bwrap"
    return None


def wrap_command(cmd: list[str]) -> tuple[list[str], str | None]:
    """(comando da eseguire, meccanismo usato o None se il comando resta com'è)."""
    w = wrapper()
    return (w[0] + cmd, w[1]) if w else (cmd, None)


def check_isolation() -> bool | None:
    """Prova che il recinto isola davvero: True = rete bloccata, False = rete ancora raggiungibile,
    None = non verificabile (nessun meccanismo, o programma installato senza interprete Python)."""
    w = wrapper()
    if w is None or getattr(sys, "frozen", False):
        return None
    try:
        out = subprocess.run(w[0] + [sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "BLOCCATA" in out:
        return True
    return False if "APERTA" in out else None


def status_line() -> str:
    w = wrapper()
    if w is None:
        if sys.platform.startswith("win"):
            return "Isolamento di rete del sistema: affidato alla regola del firewall creata dall'installer"
        return "Isolamento di rete del sistema: non disponibile su questo computer"
    return f"Isolamento di rete del sistema ({w[1]}): disponibile"
