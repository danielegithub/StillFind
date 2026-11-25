# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Blocco di ogni accesso alla rete (CercaDoc funziona solo offline)."""
from __future__ import annotations

import os
import sys


NETWORK_MODULES = (
    "socket", "_socket", "ssl", "_ssl", "socketserver", "asyncio",
    "http", "http.client", "http.server", "urllib", "urllib.request", "urllib.error",
    "urllib.parse", "ftplib", "smtplib", "imaplib", "poplib", "telnetlib", "nntplib",
    "xmlrpc", "xmlrpc.client", "webbrowser",
)


def block_network() -> None:
    """Impedisce a CercaDoc qualsiasi accesso alla rete.

    1. Se un modulo di rete è già caricato, lo rende inutilizzabile.
    2. Da qui in poi `import socket`, `import urllib.request`, ecc. falliscono
       (nell'eseguibile distribuito questi moduli non sono nemmeno inclusi).
    """
    import socket as _s  # se escluso dalla build, qui solleva ImportError: va bene

    def deny(*_a, **_k):
        raise OSError("CercaDoc funziona solo offline: connessione di rete bloccata")

    class OfflineSocket(_s.socket):
        def connect(self, *a, **k):
            deny()

        connect_ex = bind = sendto = connect
        # (i socket locali AF_UNIX non servono a questo programma)

    _s.socket = OfflineSocket
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        setattr(_s, name, deny)


def _block_network_safe() -> None:
    try:
        block_network()
    except ImportError:
        pass  # eseguibile senza modulo socket: ancora meglio
    for name in NETWORK_MODULES:
        sys.modules[name] = None  # qualunque import successivo fallisce
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(var, None)


def self_test_offline() -> bool:
    """Verifica, nel programma in esecuzione, che la rete sia davvero inaccessibile."""
    import importlib
    ok = True
    for mod in ("socket", "ssl", "urllib.request", "http.client", "ftplib", "smtplib"):
        try:
            importlib.import_module(mod)
            print(f"  [ERRORE] il modulo '{mod}' è importabile")
            ok = False
        except ImportError:
            print(f"  [ok] '{mod}' non disponibile")
    return ok
