# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Riga di comando e avvio dell'interfaccia grafica.

Uso:
    python3 -m stillfind                       # apre l'interfaccia grafica
    python3 -m stillfind --add-folder ~/Documenti
    python3 -m stillfind --index               # (re)indicizza da terminale
    python3 -m stillfind --search 'contratto AND affitto'
    python3 -m stillfind --self-test-offline

Dipendenze: nessuna obbligatoria. Per i PDF installare:  pip install pypdf
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from pathlib import Path

from stillfind.core.about import version_text
from stillfind.core.config import DB_PATH, Settings
from stillfind.core.offline import self_test_offline
from stillfind.indexing.duplicates import DuplicateFinder
from stillfind.search.engine import HIT_END, HIT_START, SearchEngine
from stillfind.search.query import Query
from stillfind.storage.database import Database
from stillfind.tools.export import CsvExporter


class CommandLine:
    """Interpreta gli argomenti e li esegue; senza argomenti apre la finestra."""

    def __init__(self, argv: list[str] | None = None):
        self.parser = self._build_parser()
        self.args = self.parser.parse_args(argv)

    # ---- argomenti
    @staticmethod
    def _build_parser() -> argparse.ArgumentParser:
        ap = argparse.ArgumentParser(description="StillFind - ricerca full-text offline")
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
        ap.add_argument("--self-test-bundle", action="store_true",
                        help="verifica modelli, OCR e librerie nell'app compilata")
        ap.add_argument("--bundle-report", metavar="FILE.json", help=argparse.SUPPRESS)
        ap.add_argument("--version", action="store_true", help="mostra versione, autore e licenza")
        ap.add_argument("--show-search", action="store_true", help="apre o porta in primo piano la ricerca")
        ap.add_argument("--configure-protection", action="store_true", help="apre la configurazione della protezione")
        ap.add_argument("--listen-shortcut", action="store_true", help=argparse.SUPPRESS)
        ap.add_argument("--tray-search", action="store_true", help=argparse.SUPPRESS)
        ap.add_argument("--session-stdin", action="store_true", help=argparse.SUPPRESS)
        ap.add_argument("--window-action", choices=("open", "settings", "about", "pause", "resume"), help=argparse.SUPPRESS)
        ap.add_argument("--run-id", help=argparse.SUPPRESS)
        ap.add_argument("--quit-token", type=int, help=argparse.SUPPRESS)
        ap.add_argument("--target", help=argparse.SUPPRESS)
        ap.add_argument("--forget", choices=("file", "folder"), help=argparse.SUPPRESS)
        ap.add_argument("--target-stdin", action="store_true", help=argparse.SUPPRESS)
        for flag in ("--retry-errors", "--force-ocr", "--rebuild", "--verify-firewall", "--parent-stdin"):
            ap.add_argument(flag, action="store_true", help=argparse.SUPPRESS)
        return ap

    # ---- esecuzione
    def run(self) -> None:
        args = self.args
        self._attach_windows_pipes()
        if args.self_test_bundle:
            from stillfind.tools.bundle_check import check
            sys.exit(check(args.bundle_report))
        if args.version:
            print(version_text())
            return

        Settings.load()  # imposta la lingua dell'interfaccia anche nei processi di servizio (icona di sistema…)
        from stillfind.core.private_tmp import PrivateWorkspace
        from stillfind.runtime.signals import AppSignals
        from stillfind.runtime.hotkey import ShortcutProcess
        if args.listen_shortcut or args.tray_search:
            sys.exit(ShortcutProcess.listen(args.tray_search))
        PrivateWorkspace.cleanup()
        if args.show_search:
            AppSignals.show()
            return
        if args.configure_protection or args.window_action:
            from stillfind.ui import app as gui
            gui.run(configure=args.configure_protection, action=args.window_action or "open", quit_token=args.quit_token)
            return
        if args.worker and args.target_stdin:
            args.target = json.loads(sys.stdin.buffer.readline())
            if not isinstance(args.target, str) or not args.target:
                sys.exit("Percorso del documento non valido.")
        if args.worker or args.embed or args.search or args.index or args.duplicates:
            self._unlock_archive()
        if args.agent or args.worker or args.embed:
            self._run_background_process()
        if args.self_test_offline:
            self._self_test_offline()

        cfg = Settings.load()
        changed = self._update_folders(cfg)
        if not (args.list or args.index or args.search or args.duplicates) and not changed:
            from stillfind.ui import app as gui
            gui.run()
            return
        if args.list:
            print(json.dumps(cfg, indent=2, ensure_ascii=False))
        if args.index:
            self._index()
        if args.duplicates:
            self._duplicates()
        if args.search:
            self._search(cfg)

    @staticmethod
    def _attach_windows_pipes() -> None:
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            # Un eseguibile senza console può comunque ricevere pipe esplicite da StillFind.
            import ctypes
            import msvcrt
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

    def _unlock_archive(self) -> None:
        """Con la cassaforte attiva la chiave arriva dal processo padre (stdin) o dalla passphrase."""
        from stillfind.storage.protection import ArchiveProtection, Locked
        args = self.args
        spec = ArchiveProtection.settings(DB_PATH)
        if spec["mode"] != "vault":
            return
        if args.session_stdin and (args.embed or args.worker):
            source = sys.stdin.buffer
            key = bytearray(source.read(67))
            try:
                if not re.fullmatch(rb"x'[0-9a-f]{64}'", key):
                    sys.exit("Sessione cassaforte non valida.")
                ArchiveProtection.remember(spec, key)
            finally:
                key[:] = b"\0" * len(key)

            def parent_lifetime():
                # os.read evita di trattenere il lock del buffer di stdin alla chiusura di Python.
                os.read(source.fileno(), 1)
                ArchiveProtection.forget()
                os._exit(0)
            threading.Thread(target=parent_lifetime, daemon=True).start()
        elif sys.stdin and sys.stdin.isatty() and not args.worker:
            from getpass import getpass
            try:
                ArchiveProtection.unlock(DB_PATH, getpass("Passphrase StillFind: "))
            except Locked as exc:
                sys.exit(str(exc))
        else:
            sys.exit("Cassaforte bloccata. Apri la finestra di StillFind per sbloccarla.")

    def _run_background_process(self) -> None:
        from stillfind.runtime import agent
        args = self.args
        if args.agent:
            sys.exit(agent.Agent.main())
        parent_closed = None
        if args.parent_stdin:
            parent_closed = threading.Event()

            def watch_parent():
                os.read(sys.stdin.fileno(), 1)
                parent_closed.set()
            threading.Thread(target=watch_parent, daemon=True).start()
        sys.exit(agent.WorkerRun.execute(args.run_id, target=args.target, retry_errors=args.retry_errors,
                                  force_ocr=args.force_ocr, rebuild=args.rebuild, mode="embed" if args.embed else "worker",
                                  verify_firewall=args.verify_firewall, quit_token=args.quit_token,
                                  parent_closed=parent_closed, forget=args.forget))

    @staticmethod
    def _self_test_offline() -> None:
        from stillfind.runtime.netlock import NetworkIsolation
        ok = self_test_offline()
        print("OFFLINE VERIFICATO" if ok else "ATTENZIONE: rete accessibile")
        print(NetworkIsolation.status_line())
        sys.exit(0 if ok else 1)

    def _update_folders(self, cfg: Settings) -> bool:
        args = self.args
        changed = False
        for f in args.add_folder or []:
            cfg.add_folder(str(Path(f).expanduser().resolve()), recursive=not args.no_subfolders)
            changed = True
        for f in args.remove_folder or []:
            changed = cfg.remove_folder(str(Path(f).expanduser().resolve())) or changed
        if args.semantic:
            cfg["semantic_search"] = args.semantic == "on"
            changed = True
        if changed:
            cfg.save()
        return changed

    @staticmethod
    def _index() -> None:
        from stillfind.runtime import agent

        def report(st):
            if st["phase"] == "error":
                print(st["error"], file=sys.stderr)
            elif st["phase"] == "done":
                print(file=sys.stderr)
                print({k: v for k, v in st.items() if k not in ("current", "phase")})
            else:
                print(f"\r{st.get('current') or st.get('scanned', 0)}   ", end="", file=sys.stderr)
        code = agent.WorkerRun.execute(mode="index", report=report)
        if code == agent.WorkerMonitor.BUSY:
            sys.exit("Indicizzazione già in corso.")
        if code:
            sys.exit(code)

    @staticmethod
    def _duplicates() -> None:
        from stillfind.core.system import human_size
        groups = DuplicateFinder(Database.connect()).find()
        for g in groups:
            print(f"{len(g['paths'])} copie · {human_size(g['size'])} ciascuna · spreco {human_size(g['wasted'])}")
            for path in g["paths"]:
                print(f"    {path}")
        print(f"{len(groups)} gruppi di duplicati" if groups else "Nessun duplicato trovato.")

    def _search(self, cfg: Settings) -> None:
        args = self.args
        con = Database.connect()
        if args.export:
            n = CsvExporter(args.export).write(self._search_rows(con, cfg, args.search, 100_000))
            print(f"{n} risultati salvati in {args.export}")
            return
        engine = SearchEngine(con)
        found = self._search_rows(con, cfg, args.search, 50)
        engine.add_pages(found)
        for r in found:
            snip = r["snippet"].replace(HIT_START, "[").replace(HIT_END, "]").replace("\n", " ")
            print(f"{r['path']}" + (f"  ({r['location']})" if r.get("location") else "") + f"\n    {snip}")
        if not found:
            hint = engine.suggest(args.search)
            print(f"Nessun risultato." + (f" Intendevi «{hint}»?" if hint else ""))

    @staticmethod
    def _search_rows(con, cfg: Settings, q: str, limit: int) -> list[dict]:
        """Ricerca per parole e, se è attiva e disponibile, anche per significato."""
        from stillfind.semantic.search import SemanticIndex, SemanticSearch
        if SemanticIndex.available(cfg) and Query.parse(q).plain_text():
            try:
                from stillfind.semantic.embedder import Embedder, EmbedderUnavailable
                return SemanticSearch(con, Embedder()).hybrid_search(q, limit=limit, min_score=float(
                    cfg.get("semantic_min_score", SemanticSearch.MIN_SCORE)))
            except EmbedderUnavailable as ex:
                print(f"Ricerca per significato non disponibile: {ex}", file=sys.stderr)
        return SearchEngine(con).search(q, limit=limit)


def main(argv: list[str] | None = None) -> None:
    CommandLine(argv).run()
