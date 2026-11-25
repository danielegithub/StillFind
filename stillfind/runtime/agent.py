# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Agente leggero e lavoratore separato.

L'agente (`python -m stillfind --agent`) controlla le cartelle ogni `auto_scan_seconds` guardando solo le
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

from stillfind.core.config import APP_DIR, DB_PATH, Settings
from stillfind.core.locks import ProcessLock
from stillfind.indexing.indexer import Indexer
from stillfind.runtime.background import PowerMonitor
from stillfind.runtime.hotkey import Registration
from stillfind.runtime.netlock import NetworkIsolation
from stillfind.runtime.signals import AppSignals
from stillfind.storage.archive import ArchiveEraser
from stillfind.storage.database import Database
from stillfind.storage.protection import ArchiveProtection


class WorkerMonitor:
    """Stato condiviso del lavoratore: avanzamento nel database, file di arresto, lock."""

    BUSY = 75  # codice di uscita: un altro lavoratore è già in corso
    ACTIVE = {"scan", "index", "embed", "forget"}

    @staticmethod
    def stop_file(run):
        return APP_DIR / ("stop-" + uuid.UUID(run).hex)

    @classmethod
    def read_status(cls, con):
        st = con.worker_status()
        if st["phase"] in cls.ACTIVE:
            # Dopo un arresto forzato il sistema rilascia il lock. Ricontrolla la riga
            # sotto lo stesso lock, prima di correggere lo stato lasciato dal figlio.
            lock = ProcessLock.acquire(APP_DIR / "worker.lock")
            if lock is not None:
                with lock:
                    st = con.worker_status()
                    if st["phase"] in cls.ACTIVE:
                        con.publish_progress(st["run"], {"phase": "error", "error": "Lavoratore interrotto."})
                        st = con.worker_status()
        return st

    @staticmethod
    def launch_failed(con, run, message):
        # Un altro avvio può aver già pubblicato il proprio avanzamento.
        lock = ProcessLock.acquire(APP_DIR / "worker.lock")
        if lock is not None:
            with lock:
                con.publish_progress(run, {"phase": "error", "error": message})


class WorkerLauncher:
    """Avvia il lavoratore come processo separato."""

    @staticmethod
    def spawn(sandbox: bool = True, cwd: str | None = None, mode: str = "--worker", *,
              run=None, target=None, retry_errors=False, force_ocr=False, rebuild=False,
              from_window=False, quit_token=None, forget=None) -> subprocess.Popen:
        """Lo avvia a priorità bassa e, se possibile, senza rete (`netlock`).
        `mode`: «--worker» (legge i documenti e poi calcola i vettori) oppure «--embed» (solo i vettori)."""
        cmd = AppSignals.command(mode)
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
            cmd = NetworkIsolation.isolated_command(cmd)
        elif sandbox:
            cmd, _ = NetworkIsolation.wrap_command(cmd)
        spec = ArchiveProtection.settings(DB_PATH)
        key = ArchiveProtection.session_key(spec) if spec["mode"] == "vault" else None
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
    """Controlla periodicamente le cartelle e avvia il lavoratore solo se serve."""

    def __init__(self, spawn=None, sleep=time.sleep, log=lambda msg: print(msg, flush=True)):
        self.sandbox = bool(Settings.load().get("network_sandbox", True))
        self.quit_token = AppSignals.quit_token()
        self.spawn = spawn or (lambda: WorkerLauncher.spawn(self.sandbox, quit_token=self.quit_token))
        self.sleep, self.log = sleep, log
        self.worker: subprocess.Popen | None = None
        self.semantic_retry_at = 0.0  # se il calcolo dei vettori non avanza, si riprova solo dopo un po'

    def _vectors_to_compute(self, cfg: dict) -> int:
        """Documenti con i vettori da calcolare (0 se la ricerca per significato è spenta, il modello manca o
        un tentativo recente non è andato a buon fine). Solo SQL: nessun modello né `numpy` caricati qui."""
        if not cfg.get("semantic_search") or time.monotonic() < self.semantic_retry_at:
            return 0
        from stillfind.semantic.search import SemanticIndex
        if not SemanticIndex.available(cfg):
            return 0
        con = Database.connect()
        try:
            return SemanticIndex(con).pending_count()
        finally:
            con.close()

    def tick(self) -> bool:
        """Un controllo. True se ha avviato il lavoratore e questo ha finito."""
        cfg = Settings.load()
        if not cfg["folders"]:
            return False
        if ArchiveProtection.settings(DB_PATH)["mode"] == "vault":
            return False
        if cfg.get("pause_on_battery") and PowerMonitor.on_battery():
            return False  # a batteria non si fa partire nulla
        with closing(Database.connect()) as con:
            st = WorkerMonitor.read_status(con)
            if st["manual_pause"] or st["phase"] in WorkerMonitor.ACTIVE:
                return False
        changes = Indexer.run_all(cfg, dry_run=True)["changes"]
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
        if code and code != WorkerMonitor.BUSY and self.sandbox and NetworkIsolation.wrapper() is not None:
            # il recinto può non partire (permessi, sistema datato): meglio un lavoratore senza recinto che nessuno
            self.sandbox = False
            self.log("il lavoratore non è partito nel recinto senza rete: la prossima volta si avvia senza")
        return True

    def _vectors_to_compute_after(self, before: int) -> bool:
        """True se, dopo il lavoratore, i documenti senza vettori non sono diminuiti."""
        self.semantic_retry_at = 0.0
        return self._vectors_to_compute(Settings.load()) >= before

    def run(self, quit_token) -> None:
        self.quit_token = quit_token
        while AppSignals.quit_token() == quit_token:
            try:
                self.tick()
            except Exception as ex:  # un errore non deve fermare l'agente
                self.log(f"errore nel controllo: {ex}")
            secs = int(Settings.load().get("auto_scan_seconds", 60) or 0)
            deadline = time.monotonic() + (max(10, secs) if secs else 600)
            while time.monotonic() < deadline and AppSignals.quit_token() == quit_token:
                if AppSignals.consume("agent-wake"):
                    break
                self.sleep(0.4)

    @classmethod
    def main(cls) -> int:
        """Punto d'ingresso del processo agente (`--agent`): scorciatoia globale, icona di sistema e controllo."""
        lock = ProcessLock.acquire(APP_DIR / "agent.lock")
        if lock is None:
            print("StillFind: un agente è già in esecuzione.", file=sys.stderr)
            return 1
        quit_token = AppSignals.quit_token()
        shortcut = Registration(lambda: Settings.load().get("global_shortcut", True), AppSignals.show,
                                       lambda msg: print(msg, file=sys.stderr))

        def tray_action(action):
            if action == "quit":
                AppSignals.quit_all()
            elif action in ("pause", "resume") and ArchiveProtection.settings(DB_PATH)["mode"] != "vault":
                with closing(Database.connect()) as con:
                    con.set_paused(action == "pause")
                if action == "resume":
                    AppSignals.signal("agent-wake")
            else:
                AppSignals.show(action)

        def tray_status():
            if ArchiveProtection.settings(DB_PATH)["mode"] == "vault":
                return None
            with closing(Database.connect()) as con:
                return WorkerMonitor.read_status(con)

        tray = Registration(lambda: not AppSignals.window_owns_tray(), tray_action, lambda _: None,
                                   tray=tray_status)
        try:
            PowerMonitor.lower_thread_priority()
            agent = cls()
            print(NetworkIsolation.status_line() if agent.sandbox
                  else "Isolamento di rete del lavoratore: disattivato nelle impostazioni", flush=True)
            agent.run(quit_token)
        except KeyboardInterrupt:
            pass
        finally:
            shortcut.close()
            tray.close()
            lock.close()
        return 0


class WorkerControl:
    """Segnali di arresto e di pausa per il lavoratore (stesso contratto di un `threading.Event`)."""

    def __init__(self, con, run, quit_token=None, parent_closed=None):
        self.parent_closed = parent_closed
        self.con = con
        self.marker = WorkerMonitor.stop_file(run)
        self.quit_token = AppSignals.quit_token() if quit_token is None else quit_token
        self.next_check = 0.0
        self.next_pause = 0.0
        self.stopped = False

    def cancelled(self):
        now = time.monotonic()
        if now >= self.next_check:
            self.next_check = now + 0.1
            self.stopped = (self.marker.exists() or AppSignals.quit_token() != self.quit_token
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


class WorkerRun:
    """Un'esecuzione del lavoratore: legge i documenti, poi calcola i vettori, pubblicando l'avanzamento."""

    FIELDS = ("phase", "done", "total", "scanned", "added", "updated", "moved", "removed", "errors",
              "passages", "seconds", "passages_per_second", "stopped", "paused")

    def __init__(self, run=None, *, target=None, retry_errors=False, force_ocr=False, rebuild=False, mode="worker",
                 verify_firewall=False, quit_token=None, parent_closed=None, report=None, forget=None):
        self.run_id = run or uuid.uuid4().hex
        self.target, self.retry_errors, self.force_ocr, self.rebuild = target, retry_errors, force_ocr, rebuild
        self.mode, self.verify_firewall, self.quit_token = mode, verify_firewall, quit_token
        self.parent_closed, self.report, self.forget = parent_closed, report, forget
        self._previous = None
        self._last = 0.0

    @classmethod
    def execute(cls, run=None, **options) -> int:
        """Esegue il lavoratore e restituisce il codice di uscita."""
        return cls(run, **options).run()

    def _publish(self, con, st, final=False):
        now = time.monotonic()
        signature = (st["phase"], st.get("ocr_pending"), st.get("paused"))
        if final or signature != self._previous or now - self._last >= 0.4:
            con.publish_progress(self.run_id, st)
            if self.report:
                self.report(st)
            else:
                print(json.dumps({k: st[k] for k in self.FIELDS if k in st}), flush=True)
            self._previous, self._last = signature, now
        elif con.in_transaction:
            con.commit()  # prima di un file lento libera il writer anche se il menu è già aggiornato

    def run(self) -> int:
        PowerMonitor.lower_thread_priority()
        lock = ProcessLock.acquire(APP_DIR / "worker.lock")
        if lock is None:
            return WorkerMonitor.BUSY
        with lock, closing(Database.connect()) as con:
            stop = WorkerControl(con, self.run_id, self.quit_token, self.parent_closed)

            def publish(st, final=False):
                self._publish(con, st, final)

            try:
                if self.forget:
                    return self._forget(con, stop, publish)
                if self.verify_firewall:
                    NetworkIsolation.verify_windows_firewall()
                cfg = Settings.load()
                publish({"phase": "embed" if self.mode == "embed" else "scan", "scanned": 0})
                if stop.is_set():
                    publish({"phase": "done", "stopped": True}, True)
                    return 0
                if self.rebuild:
                    con.reset_index()
                summary = {}
                if self.mode != "embed":
                    summary = Indexer.run_all(cfg, publish, stop, retry_errors=self.retry_errors, target=self.target,
                                              force_ocr=self.force_ocr, con=con)
                if (self.mode != "index" and not summary.get("stopped") and cfg.get("semantic_search")
                        and not stop.is_set()):
                    stats = self._embed(cfg, con, stop, publish, self.mode == "embed")
                    if self.mode == "embed":
                        summary = stats
                publish(dict(summary, phase=summary.get("phase", "embed_done") if self.mode == "embed" else "done",
                             stopped=bool(summary.get("stopped") or stop.cancelled()), ocr_pending=0), True)
                return 0
            except Exception as exc:
                con.rollback()
                publish({"phase": "error", "error": str(exc)}, True)
                return 1
            finally:
                stop.marker.unlink(missing_ok=True)

    def _forget(self, con, stop, publish) -> int:
        if self.forget not in ("file", "folder") or not self.target:
            raise ValueError("Seleziona un file o una cartella dell’archivio.")
        publish({"phase": "forget"})
        if stop.cancelled():
            publish({"phase": "done", "stopped": True}, True)
        else:
            removed = ArchiveEraser(con).forget(self.target, folder=self.forget == "folder")
            publish({"phase": "done", "forgotten": True, "removed": removed}, True)
        return 0

    @staticmethod
    def _embed(cfg, con, stop, publish, explicit):
        from stillfind.semantic.search import EmbeddingJob, SemanticIndex
        if not SemanticIndex.available(cfg):
            return {"phase": "semantic_unavailable"}
        retry_at = con.execute("SELECT riprova_vettori FROM stato WHERE id=1").fetchone()[0]
        before = SemanticIndex(con).pending_count()
        if not explicit and (not before or time.time() < retry_at):
            return
        from stillfind.semantic.embedder import Embedder, EmbedderUnavailable
        try:
            publish({"phase": "embed", "done": 0, "total": before})
            embedder = Embedder()
        except EmbedderUnavailable as exc:
            result = {"phase": "semantic_error", "error": str(exc)}
        else:
            started = time.monotonic()
            st = EmbeddingJob(cfg, embedder, publish, stop).run()
            seconds = time.monotonic() - started
            result = dict(st, phase="embed_done", seconds=round(seconds, 3),
                          passages_per_second=round(st["passages"] / seconds, 2) if seconds else 0)
        if not explicit:
            print(json.dumps(result), flush=True)
        if not stop.cancelled():
            retry_at = time.time() + 3600 if before and SemanticIndex(con).pending_count() >= before else 0
            with con:
                con.execute("UPDATE stato SET riprova_vettori=? WHERE id=1", (retry_at,))
        return result
