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


def isolated_command(cmd):
    """La GUI richiede un recinto anche se il ripiego dell'agente è abilitato."""
    if sys.platform == "win32":
        return [*cmd, "--verify-firewall"]
    wrapped, method = wrap_command(cmd)
    if method is None:
        raise RuntimeError("Indicizzazione sospesa. Il recinto senza rete non è disponibile su questo sistema.")
    return wrapped


def verify_windows_firewall():
    """Verifica le regole effettive prima della lettura, inclusi gli eseguibili esterni dell'OCR."""
    from .extractors.ocr import which
    programs = {sys.executable, getattr(sys, "_base_executable", sys.executable)}
    programs.update(path for name in ("tesseract", "pdftoppm") if (path := which(name)))
    # Le regole possono essere limitate a porte, indirizzi, servizi, utenti o interfacce.
    # Accettiamo solo blocchi in uscita generali sul programma, per tutti i profili.
    script = r"""
$ErrorActionPreference = 'Stop'
if ((Get-Service MpsSvc).Status -ne 'Running' -or (Get-Service BFE).Status -ne 'Running') { exit 1 }
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
if ($profiles.Count -ne 3 -or @($profiles | Where-Object {
    $_.Enabled -ne 'True' -or @($_.DisabledInterfaceAliases | Where-Object { $_ -and $_ -ne 'NotConfigured' }).Count
}).Count) { exit 1 }
$required = @($env:CERCADOC_FIREWALL_PROGRAMS -split '\|')
$rules = Get-NetFirewallRule -PolicyStore ActiveStore -Direction Outbound -Enabled True -Action Block
foreach ($rule in $rules) {
    if ($rule.Profile -ne 'Any' -or $rule.Platform -or $rule.PolicyAppId -or $rule.RemoteDynamicKeywordAddresses) { continue }
    if ($rule.EnforcementStatus -ne 'Full') { continue }
    $app = $rule | Get-NetFirewallApplicationFilter
    if (!$app.Program) { continue }
    $exe = [Environment]::ExpandEnvironmentVariables($app.Program)
    if ($required -notcontains $exe -or ($app.Package -and $app.Package -ne 'Any')) { continue }
    $ports = $rule | Get-NetFirewallPortFilter
    $addr = $rule | Get-NetFirewallAddressFilter
    $svc = $rule | Get-NetFirewallServiceFilter
    $iface = $rule | Get-NetFirewallInterfaceFilter
    $type = $rule | Get-NetFirewallInterfaceTypeFilter
    $sec = $rule | Get-NetFirewallSecurityFilter
    $scope = @($ports.Protocol; $ports.LocalPort; $ports.RemotePort; $addr.LocalAddress; $addr.RemoteAddress;
               $svc.Service; $iface.InterfaceAlias; $type.InterfaceType; $sec.LocalUser; $sec.RemoteUser;
               $sec.RemoteMachine)
    if (@($scope | Where-Object { $_ -and $_ -ne 'Any' }).Count) { continue }
    if ($sec.Authentication -ne 'NotRequired' -or $sec.Encryption -ne 'NotRequired') { continue }
    $required = @($required | Where-Object { $_ -ne $exe })
    if (!$required.Count) { exit 0 }
}
if ($required.Count) { exit 1 }
"""
    powershell = os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                            env=dict(os.environ, CERCADOC_FIREWALL_PROGRAMS="|".join(programs)),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("Per indicizzare occorre il firewall di Windows attivo e una regola di blocco "
                           "in uscita su tutti i profili per " + ", ".join(sorted(programs)))


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
