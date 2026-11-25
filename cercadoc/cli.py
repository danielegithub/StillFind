# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Riga di comando e avvio dell'interfaccia grafica.

Uso:
    python3 -m cercadoc                       # apre l'interfaccia grafica
    python3 -m cercadoc --add-folder ~/Documenti
    python3 -m cercadoc --index               # (re)indicizza da terminale
    python3 -m cercadoc --search 'contratto AND affitto'
    python3 -m cercadoc --self-test-offline

Dipendenze: nessuna obbligatoria. Per i PDF installare:  pip install pypdf
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import background
from .about import version_text
from .config import load_config, save_config
from .db import connect
from .duplicates import find_duplicates
from .export import export_csv
from .indexer import index_all
from .offline import self_test_offline
from .search import HIT_END, HIT_START, add_pages, plain_text_and_filters, search, suggest


def run_gui() -> None:
    from . import gui
    gui.run()


def _search_for_cli(con, cfg: dict, q: str, limit: int) -> list[dict]:
    """Ricerca per parole e, se è attiva e disponibile, anche per significato."""
    from . import semantic
    if semantic.available(cfg) and plain_text_and_filters(q)[0]:
        try:
            from .embedder import Embedder, EmbedderUnavailable
            return semantic.hybrid_search(con, q, Embedder(), limit=limit, min_score=float(
                cfg.get("semantic_min_score", semantic.MIN_SCORE)))
        except EmbedderUnavailable as ex:
            print(f"Ricerca per significato non disponibile: {ex}", file=sys.stderr)
    return search(con, q, limit=limit)


def main() -> None:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        # Un eseguibile senza console può comunque ricevere pipe esplicite da CercaDoc.
        import ctypes
        import msvcrt
        import os
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetFileType.argtypes = [wintypes.HANDLE]
        for name, number, mode, flags in (("stdin", -10, "r", os.O_RDONLY), ("stdout", -11, "w", os.O_WRONLY)):
            if getattr(sys, name) is None:
                handle = kernel.GetStdHandle(number)
                if handle and kernel.GetFileType(handle) == 3:
                    fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
                    setattr(sys, name, os.fdopen(fd, mode, encoding="utf-8"))
    ap = argparse.ArgumentParser(description="CercaDoc - ricerca full-text offline")
    ap.add_argument("--add-folder", metavar="CARTELLA", action="append", help="aggiunge una cartella da indicizzare")
    ap.add_argument("--no-subfolders", action="store_true", help="con --add-folder: non includere le sottocartelle")
    ap.add_argument("--remove-folder", metavar="CARTELLA", action="append")
    ap.add_argument("--list", action="store_true", help="mostra la configurazione")
    ap.add_argument("--index", action="store_true", help="indicizza da terminale")
    ap.add_argument("--search", metavar="QUERY", help="cerca da terminale")
    ap.add_argument("--agent", action="store_true",
                    help="controlla le cartelle in silenzio e avvia il lavoratore solo se serve")
    ap.add_argument("--worker", action="store_true", help="legge i documenti nuovi o cambiati e termina")
    ap.add_argument("--embed", action="store_true",
                    help="calcola i vettori per la ricerca per significato (se attiva) e termina")
    ap.add_argument("--semantic", choices=("on", "off"),
                    help="attiva o disattiva la ricerca per significato (serve il modello in models/)")
    ap.add_argument("--duplicates", action="store_true", help="elenca i documenti duplicati (contenuto identico)")
    ap.add_argument("--export", metavar="FILE.csv", help="con --search: salva i risultati in un file CSV")
    ap.add_argument("--self-test-offline", action="store_true",
                    help="verifica che il programma non possa usare la rete")
    ap.add_argument("--version", action="store_true", help="mostra versione, autore e licenza")
    ap.add_argument("--show-search", action="store_true", help="apre o porta in primo piano la ricerca")
    ap.add_argument("--configure-protection", action="store_true", help="apre la configurazione della protezione")
    ap.add_argument("--listen-shortcut", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--tray-search", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--session-stdin", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.version:
        print(version_text())
        return

    from . import hotkey, protection
    from .config import DB_PATH
    if args.listen_shortcut or args.tray_search:
        sys.exit(hotkey.listen(args.tray_search))
    if args.show_search:
        hotkey.show()
        return
    if args.configure_protection:
        from . import gui
        gui.run(configure=True)
        return
    if args.worker or args.embed or args.search or args.index or args.duplicates:
        spec = protection.settings(DB_PATH)
        if spec["mode"] == "vault":
            if args.session_stdin and (args.embed or args.worker):
                import re
                import os
                import threading
                source = sys.stdin.buffer
                key = bytearray(source.read(67))
                try:
                    if not re.fullmatch(rb"x'[0-9a-f]{64}'", key):
                        sys.exit("Sessione cassaforte non valida.")
                    protection.remember(spec, key)
                finally:
                    key[:] = b"\0" * len(key)
                def parent_lifetime():
                    # os.read evita di trattenere il lock del buffer di stdin alla chiusura di Python.
                    os.read(source.fileno(), 1)
                    protection.forget()
                    os._exit(0)
                threading.Thread(target=parent_lifetime, daemon=True).start()
            elif sys.stdin and sys.stdin.isatty() and not args.worker:
                from getpass import getpass
                try:
                    protection.unlock(DB_PATH, getpass("Passphrase CercaDoc: "))
                except protection.Locked as exc:
                    sys.exit(str(exc))
            else:
                sys.exit("Cassaforte bloccata. Apri la finestra di CercaDoc per sbloccarla.")

    if args.agent or args.worker or args.embed:
        from . import agent
        sys.exit(agent.run_agent() if args.agent else agent.run_worker() if args.worker else agent.run_embed())

    if args.self_test_offline:
        ok = self_test_offline()
        print("OFFLINE VERIFICATO" if ok else "ATTENZIONE: rete accessibile")
        from . import netlock
        print(netlock.status_line())
        sys.exit(0 if ok else 1)

    cfg = load_config()
    changed = False
    for f in args.add_folder or []:
        f = str(Path(f).expanduser().resolve())
        cfg["folders"] = [x for x in cfg["folders"] if x["path"] != f]
        cfg["folders"].append({"path": f, "recursive": not args.no_subfolders})
        changed = True
    for f in args.remove_folder or []:
        f = str(Path(f).expanduser().resolve())
        before = len(cfg["folders"])
        cfg["folders"] = [x for x in cfg["folders"] if x["path"] != f]
        changed = changed or len(cfg["folders"]) != before
    if args.semantic:
        cfg["semantic_search"] = args.semantic == "on"
        changed = True
    if changed:
        save_config(cfg)

    if not (args.list or args.index or args.search or args.duplicates):
        if not changed:
            run_gui()
            return
    if args.list:
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
    if args.index:
        background.lower_thread_priority()
        st = index_all(cfg, lambda s: print(f"\r{s['current'] or s['scanned']}   ", end="", file=sys.stderr))
        print(file=sys.stderr)
        print({k: v for k, v in st.items() if k not in ("current", "phase")})
    if args.duplicates:
        from .system import human_size
        groups = find_duplicates(connect())
        for g in groups:
            print(f"{len(g['paths'])} copie · {human_size(g['size'])} ciascuna · spreco {human_size(g['wasted'])}")
            for path in g["paths"]:
                print(f"    {path}")
        print(f"{len(groups)} gruppi di duplicati" if groups else "Nessun duplicato trovato.")
    if args.search:
        con = connect()
        if args.export:
            n = export_csv(_search_for_cli(con, cfg, args.search, 100_000), args.export)
            print(f"{n} risultati salvati in {args.export}")
            return
        found = _search_for_cli(con, cfg, args.search, 50)
        add_pages(con, found)
        for r in found:
            snip = r["snippet"].replace(HIT_START, "[").replace(HIT_END, "]").replace("\n", " ")
            print(f"{r['path']}" + (f"  (pag. {r['page']})" if r.get("page") else "") + f"\n    {snip}")
        if not found:
            hint = suggest(con, args.search)
            print(f"Nessun risultato." + (f" Intendevi «{hint}»?" if hint else ""))
