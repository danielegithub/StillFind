# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Agente leggero e lavoratore separato.

L'agente (`python -m cercadoc --agent`) controlla le cartelle ogni `auto_scan_seconds` guardando solo le
date dei file (niente lettura dei documenti) e avvia il **lavoratore** (`--worker`) solo se trova differenze.
Il lavoratore è un processo a parte: quando finisce, tutta la memoria usata torna al sistema.

L'agente non importa `pypdf`, Tk, lo stemmer né i modelli: resta nell'ordine di poche decine di MB, e tiene
aperta la connessione al database solo durante il controllo."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path

from . import archive, background, db, hotkey, netlock, protection
from .config import APP_DIR, DB_PATH, load_config
from .db import connect
from .indexer import index_all

LOCK_PATH = APP_DIR / "agent.lock"


def acquire_lock(path=None, *, blocking=False):
    """Un solo agente alla volta. Restituisce il file di lock da tenere aperto, o None se ce n'è già uno."""
    path = Path(path or LOCK_PATH)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    f = path.open("a+")
    try:
        if sys.platform.startswith("win"):
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except OSError:
        f.close()
        if blocking:
            raise
        return None
    return f


def spawn_worker(sandbox: bool = True, cwd: str | None = None, mode: str = "--worker", *,
                 run=None, target=None, retry_errors=False, force_ocr=False, rebuild=False,
                 from_window=False, quit_token=None, forget=None) -> subprocess.Popen:
    """Avvia il lavoratore come processo separato, a priorità bassa e, se possibile, senza rete (`netlock`).
    `mode`: «--worker» (legge i documenti e poi calcola i vettori) oppure «--embed» (solo i vettori)."""
    cmd = hotkey.command(mode)
    if run:
        cmd.extend(["--run-id", run])
    if quit_token is not None:
        cmd.extend(["--quit-token", str(quit_token)])
    if target:
        cmd.extend(["--target-stdin"] if from_window else ["--target", target])
    for flag, enabled in (("--retry-errors", retry_errors), ("--force-ocr", force_ocr), ("--rebuild", rebuild)):
        if enabled:
            cmd.append(flag)
    if forget:
        cmd.extend(["--forget", forget])  # Solo manutenzione del DB, anche a indicizzazione sospesa.
    if from_window and not forget:
        cmd = netlock.isolated_command(cmd)
    elif sandbox:
        cmd, _ = netlock.wrap_command(cmd)
    spec = protection.settings(DB_PATH)
    key = protection.session_key(spec) if spec["mode"] == "vault" else None
    if key:
        cmd.append("--session-stdin")
    elif from_window:
        cmd.append("--parent-stdin")
    kwargs = dict(stdout=subprocess.DEVNULL if from_window else subprocess.PIPE, stderr=subprocess.DEVNULL,
                  text=True, cwd=cwd)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                   | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    if key is None and not from_window:
        return subprocess.Popen(cmd, **kwargs)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, **kwargs)
        try:
            if from_window and target:
                proc.stdin.buffer.write(json.dumps(target).encode("ascii") + b"\n")
            if key:
                proc.stdin.buffer.write(key)
            proc.stdin.buffer.flush()  # Il figlio sorveglia la stessa pipe per tutta la sessione.
        except BaseException:
            proc.kill()
            proc.wait()
            try:
                proc.stdin.close()
            except OSError:
                pass
            raise
        return proc
    finally:
        if key:
            key[:] = b"\0" * len(key)


class Agent:
    def __init__(self, spawn=None, sleep=time.sleep, log=lambda msg: print(msg, flush=True)):
        self.sandbox = bool(load_config().get("network_sandbox", True))
        self.quit_token = hotkey.quit_token()
        self.spawn = spawn or (lambda: spawn_worker(self.sandbox, quit_token=self.quit_token))
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
        with closing(connect()) as con:
            st = read_status(con)
            if st["manual_pause"] or st["phase"] in ACTIVE:
                return False
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
        if getattr(self.worker, "stdout", None):
            self.worker.stdout.close()
        self.worker = None
        if vectors and self._vectors_to_compute_after(vectors):
            self.semantic_retry_at = time.monotonic() + 3600  # nessun progresso (modello rifiutato?): pausa di un'ora
        if code and code != BUSY and self.sandbox and netlock.wrapper() is not None:
            # il recinto può non partire (permessi, sistema datato): meglio un lavoratore senza recinto che nessuno
            self.sandbox = False
            self.log("il lavoratore non è partito nel recinto senza rete: la prossima volta si avvia senza")
        return True

    def _vectors_to_compute_after(self, before: int) -> bool:
        """True se, dopo il lavoratore, i documenti senza vettori non sono diminuiti."""
        self.semantic_retry_at = 0.0
        return self._vectors_to_compute(load_config()) >= before

    def run(self, quit_token) -> None:
        self.quit_token = quit_token
        while hotkey.quit_token() == quit_token:
            try:
                self.tick()
            except Exception as ex:  # noqa: BLE001 - un errore non deve fermare l'agente
                self.log(f"errore nel controllo: {ex}")
            secs = int(load_config().get("auto_scan_seconds", 60) or 0)
            deadline = time.monotonic() + (max(10, secs) if secs else 600)
            while time.monotonic() < deadline and hotkey.quit_token() == quit_token:
                if hotkey.consume("agent-wake"):
                    break
                self.sleep(0.4)


def run_agent() -> int:
    lock = acquire_lock()
    if lock is None:
        print("CercaDoc: un agente è già in esecuzione.", file=sys.stderr)
        return 1
    quit_token = hotkey.quit_token()
    shortcut = hotkey.Registration(lambda: load_config().get("global_shortcut", True), hotkey.show,
                                   lambda msg: print(msg, file=sys.stderr))

    def tray_action(action):
        if action == "quit":
            hotkey.quit_all()
        elif action in ("pause", "resume") and protection.settings(DB_PATH)["mode"] != "vault":
            with closing(connect()) as con:
                db.set_paused(con, action == "pause")
            if action == "resume":
                hotkey.signal("agent-wake")
        else:
            hotkey.show(action)

    def tray_status():
        if protection.settings(DB_PATH)["mode"] == "vault":
            return None
        with closing(connect()) as con:
            return read_status(con)

    tray = hotkey.Registration(lambda: not hotkey.window_owns_tray(), tray_action, lambda _: None, tray=tray_status)
    try:
        background.lower_thread_priority()
        agent = Agent()
        print(netlock.status_line() if agent.sandbox else "Isolamento di rete del lavoratore: disattivato nelle impostazioni",
              flush=True)
        agent.run(quit_token)
    except KeyboardInterrupt:
        pass
    finally:
        shortcut.close()
        tray.close()
        lock.close()
    return 0


BUSY = 75
ACTIVE = {"scan", "index", "embed", "forget"}


def read_status(con):
    st = db.worker_status(con)
    if st["phase"] in ACTIVE:
        # Dopo un arresto forzato il sistema rilascia il lock. Ricontrolla la riga
        # sotto lo stesso lock, prima di correggere lo stato lasciato dal figlio.
        lock = acquire_lock(APP_DIR / "worker.lock")
        if lock is not None:
            with lock:
                st = db.worker_status(con)
                if st["phase"] in ACTIVE:
                    db.publish_progress(con, st["run"], {"phase": "error", "error": "Lavoratore interrotto."})
                    st = db.worker_status(con)
    return st


def launch_failed(con, run, message):
    # Un altro avvio può aver già pubblicato il proprio avanzamento.
    lock = acquire_lock(APP_DIR / "worker.lock")
    if lock is not None:
        with lock:
            db.publish_progress(con, run, {"phase": "error", "error": message})


def stop_file(run):
    return APP_DIR / ("stop-" + uuid.UUID(run).hex)


class WorkerControl:
    def __init__(self, con, run, quit_token=None, parent_closed=None):
        self.parent_closed = parent_closed
        self.con = con
        self.marker = stop_file(run)
        self.quit_token = hotkey.quit_token() if quit_token is None else quit_token
        self.next_check = 0.0
        self.next_pause = 0.0
        self.stopped = False

    def cancelled(self):
        now = time.monotonic()
        if now >= self.next_check:
            self.next_check = now + 0.1
            self.stopped = (self.marker.exists() or hotkey.quit_token() != self.quit_token
                            or bool(self.parent_closed and self.parent_closed.is_set()))
        return self.stopped

    def is_set(self):
        now = time.monotonic()
        if now >= self.next_pause and not self.cancelled():
            # I punti di arresto sono tra documenti, pagine OCR e lotti di vettori.
            while self.con.execute("SELECT pausa FROM stato WHERE id=1").fetchone()[0] and not self.cancelled():
                self.con.commit()
                time.sleep(0.1)
            self.next_pause = time.monotonic() + 0.1
        return self.cancelled()

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while not self.is_set() and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        return self.stopped


def run_worker(run=None, *, target=None, retry_errors=False, force_ocr=False, rebuild=False, mode="worker",
               verify_firewall=False, quit_token=None, parent_closed=None, report=None, forget=None):
    background.lower_thread_priority()
    run = run or uuid.uuid4().hex
    lock = acquire_lock(APP_DIR / "worker.lock")
    if lock is None:
        return BUSY
    with lock, closing(connect()) as con:
        stop = WorkerControl(con, run, quit_token, parent_closed)
        previous = None
        last = 0.0

        def publish(st, final=False):
            nonlocal previous, last
            now = time.monotonic()
            signature = (st["phase"], st.get("ocr_pending"), st.get("paused"))
            if final or signature != previous or now - last >= 0.4:
                db.publish_progress(con, run, st)
                fields = ("phase", "done", "total", "scanned", "added", "updated", "moved", "removed", "errors",
                          "passages", "seconds", "passages_per_second", "stopped", "paused")
                if report:
                    report(st)
                else:
                    print(json.dumps({k: st[k] for k in fields if k in st}), flush=True)
                previous, last = signature, now
            elif con.in_transaction:
                con.commit()  # prima di un file lento libera il writer anche se il menu è già aggiornato

        try:
            if forget:
                if forget not in ("file", "folder") or not target:
                    raise ValueError("Seleziona un file o una cartella dell’archivio.")
                publish({"phase": "forget"})
                if stop.cancelled():
                    publish({"phase": "done", "stopped": True}, True)
                else:
                    removed = archive.forget(con, target, folder=forget == "folder")
                    publish({"phase": "done", "forgotten": True, "removed": removed}, True)
                return 0
            if verify_firewall:
                netlock.verify_windows_firewall()
            cfg = load_config()
            publish({"phase": "embed" if mode == "embed" else "scan", "scanned": 0})
            if stop.is_set():
                publish({"phase": "done", "stopped": True}, True)
                return 0
            if rebuild:
                db.reset_index(con)
            summary = {}
            if mode != "embed":
                summary = index_all(cfg, publish, stop, retry_errors=retry_errors, target=target,
                                    force_ocr=force_ocr, con=con)
            if mode != "index" and not summary.get("stopped") and cfg.get("semantic_search") and not stop.is_set():
                stats = _embed(cfg, con, stop, publish, mode == "embed")
                if mode == "embed":
                    summary = stats
            publish(dict(summary, phase=summary.get("phase", "embed_done") if mode == "embed" else "done",
                         stopped=bool(summary.get("stopped") or stop.cancelled()), ocr_pending=0), True)
            return 0
        except Exception as exc:
            con.rollback()
            publish({"phase": "error", "error": str(exc)}, True)
            return 1
        finally:
            stop.marker.unlink(missing_ok=True)


def _embed(cfg, con, stop, publish, explicit):
    from . import semantic
    if not semantic.available(cfg):
        return {"phase": "semantic_unavailable"}
    retry_at = con.execute("SELECT riprova_vettori FROM stato WHERE id=1").fetchone()[0]
    before = semantic.pending_count(con)
    if not explicit and (not before or time.time() < retry_at):
        return
    from .embedder import Embedder, EmbedderUnavailable
    try:
        publish({"phase": "embed", "done": 0, "total": before})
        embedder = Embedder()
    except EmbedderUnavailable as exc:
        result = {"phase": "semantic_error", "error": str(exc)}
    else:
        started = time.monotonic()
        st = semantic.embed_pending(cfg, embedder, publish, stop)
        seconds = time.monotonic() - started
        result = dict(st, phase="embed_done", seconds=round(seconds, 3),
                      passages_per_second=round(st["passages"] / seconds, 2) if seconds else 0)
    if not explicit:
        print(json.dumps(result), flush=True)
    if not stop.cancelled():
        retry_at = time.time() + 3600 if before and semantic.pending_count(con) >= before else 0
        with con:
            con.execute("UPDATE stato SET riprova_vettori=? WHERE id=1", (retry_at,))
    return result
