# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Agente leggero e lavoratore separato.

L'agente (`python -m cercadoc --agent`) controlla le cartelle ogni `auto_scan_seconds` guardando solo le
date dei file (niente lettura dei documenti) e avvia il **lavoratore** (`--worker`) solo se trova differenze.
Il lavoratore è un processo a parte: quando finisce, tutta la memoria usata torna al sistema.

L'agente non importa `pypdf`, Tk, lo stemmer né i modelli: resta nell'ordine di poche decine di MB, e tiene
aperta la connessione al database solo durante il controllo. L'icona nella barra di sistema si aggiungerà
sopra a questa stessa logica."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from . import background, hotkey, netlock, protection
from .config import APP_DIR, DB_PATH, load_config
from .db import connect
from .indexer import index_all

LOCK_PATH = APP_DIR / "agent.lock"


def acquire_lock(path=None):
    """Un solo agente alla volta. Restituisce il file di lock da tenere aperto, o None se ce n'è già uno."""
    APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    f = open(path or LOCK_PATH, "a+")
    try:
        if sys.platform.startswith("win"):
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def spawn_worker(sandbox: bool = True, cwd: str | None = None, mode: str = "--worker") -> subprocess.Popen:
    """Avvia il lavoratore come processo separato, a priorità bassa e, se possibile, senza rete (`netlock`).
    `mode`: «--worker» (legge i documenti e poi calcola i vettori) oppure «--embed» (solo i vettori)."""
    cmd = [sys.executable] + ([] if getattr(sys, "frozen", False) else ["-m", "cercadoc"]) + [mode]
    spec = protection.settings(DB_PATH)
    key = protection.session_key(spec) if spec["mode"] == "vault" else None
    if key:
        cmd.append("--session-stdin")
    if sandbox:
        cmd, _ = netlock.wrap_command(cmd)
    kwargs: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "text": True, "cwd": cwd}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                   | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    if key is None:
        return subprocess.Popen(cmd, **kwargs)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, **kwargs)
        try:
            proc.stdin.buffer.write(key)
            proc.stdin.buffer.flush()  # EOF segnalerà al figlio la fine della sessione anche in caso di crash.
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        return proc
    finally:
        key[:] = b"\0" * len(key)


class Agent:
    def __init__(self, spawn=None, sleep=time.sleep, log=lambda msg: print(msg, flush=True)):
        self.sandbox = bool(load_config().get("network_sandbox", True))
        self.spawn = spawn or (lambda: spawn_worker(self.sandbox))
        self.sleep, self.log = sleep, log
        self.worker: subprocess.Popen | None = None
        self.semantic_retry_at = 0.0  # se il calcolo dei vettori non avanza, si riprova solo dopo un po'

    def _vectors_to_compute(self, cfg: dict) -> int:
        """Documenti con i vettori da calcolare (0 se la ricerca per significato è spenta, il modello manca o
        un tentativo recente non è andato a buon fine). Solo SQL: nessun modello né `numpy` caricati qui."""
        if not cfg.get("semantic_search") or time.monotonic() < self.semantic_retry_at:
            return 0
        from . import semantic
        if not semantic.available(cfg):
            return 0
        con = connect()
        try:
            return semantic.pending_count(con)
        finally:
            con.close()

    def tick(self) -> bool:
        """Un controllo. True se ha avviato il lavoratore e questo ha finito."""
        cfg = load_config()
        if not cfg["folders"]:
            return False
        if protection.settings(DB_PATH)["mode"] == "vault":
            return False
        if cfg.get("pause_on_battery") and background.on_battery():
            return False  # a batteria non si fa partire nulla
        changes = index_all(cfg, dry_run=True)["changes"]
        vectors = 0 if changes else self._vectors_to_compute(cfg)  # con file da leggere i vettori seguono dopo
        if not changes and not vectors:
            return False
        self.log(f"{changes} file da leggere: avvio il lavoratore" if changes
                 else f"{vectors} documenti senza vettori di significato: avvio il lavoratore")
        self.worker = self.spawn()
        if getattr(self.worker, "stdout", None):
            for _ in self.worker.stdout:  # si legge l'avanzamento: la pipe, se piena, bloccherebbe il lavoratore
                pass
        code = self.worker.wait()
        self.worker = None
        if vectors and self._vectors_to_compute_after(vectors):
            self.semantic_retry_at = time.monotonic() + 3600  # nessun progresso (modello rifiutato?): pausa di un'ora
        if code and self.sandbox and netlock.wrapper() is not None:
            # il recinto può non partire (permessi, sistema datato): meglio un lavoratore senza recinto che nessuno
            self.sandbox = False
            self.log("il lavoratore non è partito nel recinto senza rete: la prossima volta si avvia senza")
        return True

    def _vectors_to_compute_after(self, before: int) -> bool:
        """True se, dopo il lavoratore, i documenti senza vettori non sono diminuiti."""
        self.semantic_retry_at = 0.0
        return self._vectors_to_compute(load_config()) >= before

    def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as ex:  # noqa: BLE001 - un errore non deve fermare l'agente
                self.log(f"errore nel controllo: {ex}")
            secs = int(load_config().get("auto_scan_seconds", 60) or 0)
            self.sleep(max(10, secs) if secs else 600)  # 0 = mai: si ricontrolla solo ogni tanto


def run_agent() -> int:
    lock = acquire_lock()
    if lock is None:
        print("CercaDoc: un agente è già in esecuzione.", file=sys.stderr)
        return 1
    shortcut = hotkey.Registration(lambda: load_config().get("global_shortcut", True), hotkey.show,
                                   lambda msg: print(msg, file=sys.stderr))
    tray = hotkey.Registration(lambda: True, hotkey.show, lambda _: None, tray=True)
    try:
        background.lower_thread_priority()
        agent = Agent()
        print(netlock.status_line() if agent.sandbox else "Isolamento di rete del lavoratore: disattivato nelle impostazioni",
              flush=True)
        agent.run()
    except KeyboardInterrupt:
        pass
    finally:
        shortcut.close()
        tray.close()
        lock.close()
    return 0


def run_worker() -> int:
    """Lavoratore: legge i documenti nuovi o cambiati, scrive l'avanzamento (una riga JSON) e termina."""
    background.lower_thread_priority()
    cfg = load_config()
    last = [0.0]

    def progress(st: dict) -> None:
        now = time.monotonic()
        if now - last[0] > 1.0:
            last[0] = now
            print(json.dumps({k: st[k] for k in ("phase", "done", "total", "scanned") if k in st}), flush=True)

    st = index_all(cfg, progress)
    print(json.dumps({"phase": "done", **{k: st[k] for k in ("added", "updated", "moved", "removed", "errors")}}),
          flush=True)
    if not st.get("stopped"):
        run_embed(cfg)
    return 0


def run_embed(cfg: dict | None = None) -> int:
    """Calcola i vettori di significato dei documenti che ne sono privi (se la funzione è attiva) e termina.
    Scrive una riga JSON al secondo con l'avanzamento. Con la funzione spenta o il modello assente non fa nulla."""
    background.lower_thread_priority()
    cfg = cfg or load_config()
    if not cfg.get("semantic_search"):
        return 0
    from . import semantic
    if not semantic.available(cfg):
        print(json.dumps({"phase": "semantic_unavailable"}), flush=True)
        return 0
    from .embedder import Embedder, EmbedderUnavailable
    try:
        embedder = Embedder()  # controlla le impronte del modello e del tokenizer prima di caricarli
    except EmbedderUnavailable as ex:
        print(json.dumps({"phase": "semantic_error", "error": str(ex)}), flush=True)
        return 0
    last = [0.0]

    def progress(st: dict) -> None:
        now = time.monotonic()
        if now - last[0] > 1.0:
            last[0] = now
            print(json.dumps({k: st[k] for k in ("phase", "done", "total", "passages", "paused") if k in st}),
                  flush=True)

    start = time.monotonic()
    st = semantic.embed_pending(cfg, embedder, progress)
    elapsed = time.monotonic() - start
    print(json.dumps({"phase": "embed_done", "seconds": round(elapsed, 3),
                      "passages_per_second": round(st["passages"] / elapsed, 2) if elapsed else 0,
                      **{k: st[k] for k in ("done", "total", "passages", "removed")}}),
          flush=True)
    return 0
