# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Indicizzazione incrementale delle cartelle (scansione, estrazione, salvataggio, file spostati)."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import threading
import time
from contextlib import closing
from pathlib import Path

from stillfind.core.problems import ReadProblems

from stillfind.core.ocr_languages import OcrLanguages
from stillfind.core.exclusions import Exclusions
from stillfind.core.errors import Stopped
from stillfind.core.i18n import tr
from stillfind.core.private_tmp import PrivateWorkspace
from stillfind.core.stemmer import stem_text
from stillfind.core.system import human_size, short_path
from stillfind.extractors import registry
from stillfind.extractors.ocr import ExternalTool, OcrCache, Tesseract
from stillfind.indexing.duplicates import FileHasher
from stillfind.indexing.relations import RelationGraph
from stillfind.indexing.sources import Sources
from stillfind.runtime.background import PowerMonitor
from stillfind.storage.archive import ArchiveEraser, PathKey
from stillfind.storage.database import Database

IMAGE_EXTS = registry.image_extensions


class FileFingerprint:
    """Impronta del contenuto: dimensione + primi e ultimi 64 KB."""

    @staticmethod
    def of(path: str, size: int) -> str:
        """Riconosce lo stesso file anche dopo rinomina o spostamento, senza rileggerlo tutto."""
        h = hashlib.sha1(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(65536))
            if size > 131072:
                f.seek(-65536, os.SEEK_END)
                h.update(f.read(65536))
        return h.hexdigest()


class DependencyProbe:
    """Verifica, senza importarle, le dipendenze opzionali di lettura."""

    _found: set[str] = set()  # un «no» non si ricorda: potrebbero installarle mentre l'app è aperta

    @classmethod
    def pypdf_available(cls) -> bool:
        """pypdf è installato? Si guarda senza importarlo (importarlo costa RAM, e l'agente non deve farlo)."""
        if "pypdf" in cls._found:
            return True
        if importlib.util.find_spec("pypdf") is None:
            return False
        cls._found.add("pypdf")
        return True

    @classmethod
    def pdf_render_available(cls) -> bool:
        """Esiste un modo per disegnare le pagine dei PDF per l'OCR (pdftoppm, pypdfium2 o PyMuPDF)?"""
        if "render" in cls._found:
            return True
        found = ExternalTool.which("pdftoppm") is not None or importlib.util.find_spec("pypdfium2") is not None
        if not found:
            try:
                import fitz
                found = True
            except ImportError:
                pass
        if found:
            cls._found.add("render")
        return found

    @classmethod
    def should_retry_error(cls, err: str, ocr_on: bool) -> bool:
        """Ritenta un errore se la dipendenza che mancava ora c'è (si controlla solo se l'errore la riguarda)."""
        if not err:
            return False
        low = err.lower()
        if "pypdf" in low and cls.pypdf_available():
            return True
        if ocr_on and ("tesseract" in low or ("pdftoppm" in low and cls.pdf_render_available())):
            return True
        return False


class DocumentStore:
    """Scrive nell'archivio i documenti letti: testo, pagine, relazioni."""

    def __init__(self, con):
        self.con = con

    def save(self, path, name, folder, ext, st_, content, ocr, fp, error: str = "",
             blank_pages: int = 0, meta: str = "", pages=(), info=None) -> str:
        con = self.con
        notext = int(not content.strip())  # i metadati non contano: un PDF scansionato con titolo resta «senza testo»
        if meta:
            content = meta + "\n\n" + content
        info = info or {}
        ocr_lang = info.get("ocr_lang", "")
        vals = (name, folder, ext, st_.st_size, st_.st_mtime, notext, int(ocr), blank_pages,
                st_.st_dev, st_.st_ino, fp, error, ocr_lang)
        if not con.in_transaction:
            con.execute("BEGIN IMMEDIATE")
        existing = con.execute("SELECT id FROM docs WHERE path=?", (path,)).fetchone()
        if existing:
            doc_id = existing[0]
            con.execute("UPDATE docs SET name=?, folder=?, ext=?, size=?, mtime=?, notext=?, ocr=?, "
                        "blank_pages=?, dev=?, ino=?, fp=?, error=?, ocr_lang=? WHERE id=?", vals + (doc_id,))
            con.execute("DELETE FROM fts WHERE rowid=?", (doc_id,))
            con.execute("DELETE FROM vec_done WHERE doc_id=?", (doc_id,))  # testo cambiato: vettori da ricalcolare
            con.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
            kind = "updated"
        else:
            doc_id = con.execute(
                "INSERT INTO docs(path,name,folder,ext,size,mtime,notext,ocr,blank_pages,dev,ino,fp,error,ocr_lang) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (path,) + vals).lastrowid
            kind = "added"
        # radici separate (contenuto / nome): servono a «Cerca in: Nel contenuto» e «Nel nome»
        con.execute("INSERT INTO fts(rowid,name,content,stem,nstem) VALUES(?,?,?,?,?)",
                    (doc_id, name, content, stem_text(content), stem_text(name)))
        shift = len(meta) + 2 if meta else 0
        sections = info.get("sections", [(s, "page", str(n), "") for n, s in enumerate(pages, 1)])
        page_langs = info.get("page_langs", {})
        rows = [(doc_id, n, s + shift, kind_, title, state, page_langs.get(n, "") if kind_ == "page" else "")
                for n, (s, kind_, title, state) in enumerate(sections, 1) if s + shift <= len(content)]
        con.executemany("INSERT INTO pages(doc_id,n,start,tipo,titolo,lettura,lingua) VALUES(?,?,?,?,?,?,?)", rows)
        con.execute("UPDATE docs SET struttura=1 WHERE id=?", (doc_id,))
        starts = [(n if typ == "page" else None, s) for _, n, s, typ, _, _, _ in rows]
        policy = con.execute("SELECT valore FROM regole_grafo WHERE id=1").fetchone()[0]
        RelationGraph(con).index_document(doc_id, content, starts, ext, info, policy, shift)
        if ext in ("eml", "zip"):
            con.execute("INSERT OR REPLACE INTO container_state VALUES(?,?)",
                        (doc_id, int(info.get("container_ocr", False))))
        return kind

    def backfill(self, doc_id, cfg) -> None:
        """Completa dal testo conservato anche le schede di un disco scollegato, senza rifare l'OCR."""
        con = self.con
        if not con.in_transaction:
            con.execute("BEGIN IMMEDIATE")
        row = con.execute("""SELECT f.content,d.ext,d.path,d.size,d.mtime,a.sha256,a.contenuto_sha256,
            a.formato,a.inizio_corpo,a.data_iso,a.fonte FROM docs d JOIN fts f ON f.rowid=d.id
            LEFT JOIN analisi a ON a.doc_id=d.id WHERE d.id=?""", (doc_id,)).fetchone()
        if not row:
            return
        content, ext, path, size, mtime, digest, inner, fmt, shift, stamp, source = row
        info = {"sha256": digest, "inner_sha256": inner, "inner_ext": fmt or ext}
        if stamp and source in ("FatturaPA", "email", "metadati"):
            info["document_date"] = stamp, source
        if not digest and size <= cfg.get("max_file_mb", 50) * 1024**2:
            from stillfind.extractors.meta import MetadataReader
            try:
                st = os.stat(path)
                if st.st_size == size and st.st_mtime == mtime:
                    info["sha256"] = FileHasher.sha256(path)
                    meta = MetadataReader.read(path, ext, info)
                    if meta and content.startswith(meta + "\n\n"):
                        shift = len(meta) + 2
            except OSError:
                pass
        pages = list(con.execute("SELECT CASE WHEN tipo='page' THEN n END,start FROM pages WHERE doc_id=? ORDER BY n",
                                 (doc_id,)))
        policy = con.execute("SELECT valore FROM regole_grafo WHERE id=1").fetchone()[0]
        RelationGraph(con).index_document(doc_id, content, pages, ext, info, policy, shift or 0)

    def move(self, doc_id, path, name, folder, st_) -> None:
        """Stesso file con nome/posizione diversi: aggiorna solo i dati, senza rileggerlo."""
        self.con.execute("UPDATE docs SET path=?, name=?, folder=?, mtime=?, dev=?, ino=? WHERE id=?",
                         (path, name, folder, st_.st_mtime, st_.st_dev, st_.st_ino, doc_id))
        self.con.execute("UPDATE fts SET name=?, nstem=? WHERE rowid=?", (name, stem_text(name), doc_id))

    def delete(self, doc_id) -> None:
        con = self.con
        con.execute("DELETE FROM docs WHERE id=?", (doc_id,))
        con.execute("DELETE FROM fts WHERE rowid=?", (doc_id,))
        con.execute("DELETE FROM vec_done WHERE doc_id=?", (doc_id,))  # i vettori si tolgono al prossimo calcolo
        con.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))


class Indexer:
    """Indicizzazione incrementale.

    Con `dry_run=True` si ferma dopo la fase 1 senza leggere né scrivere nulla e riporta in `changes`
    quanti file sono da leggere o da togliere: serve all'agente per decidere se avviare il lavoratore.

    Fase 1 (`_scan`): scorre le cartelle (con o senza sottocartelle) e costruisce l'albero.
    Fase 2 (`_process`): legge solo i file nuovi/modificati; un file rinominato o spostato (anche
            tra cartelle indicizzate diverse) viene riconosciuto e non riletto.
    Poi (`_cleanup`) toglie dall'indice i file davvero spariti."""

    POWER_POLL_SECONDS = 2.0  # pausa tra un controllo e l'altro della batteria; si abbassa nei test
    KNOWN_COLUMNS = ("id", "path", "mtime", "size", "notext", "ocr", "blank_pages", "dev", "ino", "fp", "ext",
                     "error", "regole", "sha256", "has_pages", "container_ocr", "struttura", "next_ocr", "ocr_lang")

    def __init__(self, con, cfg: dict, progress=None, stop: threading.Event | None = None,
                 retry_errors: bool = False, dry_run: bool = False, target: str | None = None,
                 force_ocr: bool = False):
        self.con, self.cfg, self.progress, self.stop = con, cfg, progress, stop
        self.retry_errors, self.dry_run, self.target, self.force_ocr = retry_errors, dry_run, target, force_ocr
        self.store = DocumentStore(con)
        self.reocr = False  # fase di rielaborazione OCR: ricalcola solo i documenti già letti con altre lingue
        self.seen: set[str] = set()
        self.seen_dirs: dict[str, str | None] = {}
        self.todo: list[tuple] = []
        self.reconnected: set = set()
        self.ocr_queue: set[str] = set()
        self.orphans: dict[str, dict] = {}
        self.sources: Sources | None = None
        self.st = {"scanned": 0, "added": 0, "updated": 0, "moved": 0, "removed": 0, "errors": 0,
                   "missing": [], "current": "", "phase": "scan", "total": 0, "done": 0, "paused": False}
        self._last_report = 0.0

    @classmethod
    def run_all(cls, cfg: dict, progress=None, stop: threading.Event | None = None,
                retry_errors: bool = False, dry_run: bool = False, target: str | None = None,
                force_ocr: bool = False, *, con=None) -> dict:
        """Apre l'archivio (se non è già aperto) ed esegue l'indicizzazione."""
        args = (cfg, progress, stop, retry_errors, dry_run, target, force_ocr)
        if con is None:
            with closing(Database.connect()) as own:
                return cls(own, *args).run()
        return cls(con, *args).run()

    # ---- avanzamento
    def _report(self, force: bool = False) -> None:
        now = time.monotonic()
        if self.progress and (force or now - self._last_report > 0.1):
            self._last_report = now
            self.st["ocr_pending"] = len(self.ocr_queue)
            self.progress(dict(self.st))

    def _stopped(self) -> bool:
        if self.stop is not None and self.stop.is_set():
            self.con.commit()
            self.st["stopped"] = True
            return True
        return False

    # ---- esecuzione
    def run(self) -> dict:
        self._prepare()
        if not self._scan():
            return self.st
        self._find_orphans()
        if self.dry_run:
            self._count_changes()
            return self.st
        if not self._process():
            return self.st
        if self.target:
            self.con.commit()
            self.st["current"], self.st["phase"] = "", "done"
            return self.st
        self._cleanup()
        if not self.st.get("stopped") and not self.force_ocr:
            self._reprocess_ocr()
        return self.st

    def _prepare(self) -> None:
        con, cfg = self.con, self.cfg
        if self.force_ocr:
            if not self.target or Path(self.target).suffix.lower().lstrip(".") not in IMAGE_EXTS | {"pdf"}:
                raise ValueError(tr("Scegli un PDF o un’immagine per l’OCR."))
            if not Tesseract.available():
                raise RuntimeError(tr("Tesseract non disponibile."))
            cfg = self.cfg = dict(cfg, ocr=True, _force_ocr=True)
        self.exts = {e.lower().lstrip(".") for e in cfg["extensions"]}
        self.ocr_on = bool(cfg.get("ocr")) and Tesseract.available()
        if self.ocr_on:
            self.exts |= IMAGE_EXTS
        self.excl = Exclusions(cfg["exclude_dirs"], cfg.get("exclude_files", ()))
        self.max_bytes = max(1, int(cfg.get("max_file_mb", 50) or 50)) * 1024 * 1024
        self.max_chars = cfg["max_chars_per_file"]
        self.policy = RelationGraph.rules(cfg)
        excluded = list(con.execute("SELECT path, cartella FROM esclusioni"))
        self.excluded_files = {p for p, folder in excluded if not folder}
        self.excluded_dirs = {p for p, folder in excluded if folder}
        private_root = PathKey.of(os.path.realpath(PrivateWorkspace.root_path()))
        self.folders = [f for f in cfg["folders"] if not self._skip_folder(f["path"])
                        and not PathKey.within(PathKey.of(os.path.realpath(os.path.expanduser(f["path"]))),
                                               private_root)]
        self.sources = None if self.target else Sources(con, self.folders, cfg.get("offline_retention_days", 0))
        self.pending_vectors = bool(con.execute("SELECT 1 FROM vettori_da_togliere LIMIT 1").fetchone())
        if not self.dry_run and self.pending_vectors:
            ArchiveEraser(con).finish_vector_removal()
        if not self.dry_run:
            RelationGraph(con).set_rules(cfg)
            if self.sources:
                self.sources.save(con)
            con.commit()
        self.known = self._load_known(self.target)

    def _load_known(self, path: str | None = None) -> dict[str, dict]:
        """Stato registrato dei documenti (tutti, oppure solo `path`)."""
        return {r[1]: dict(zip(self.KNOWN_COLUMNS, r)) for r in self.con.execute(
            "SELECT d.id,path,mtime,size,notext,ocr,blank_pages,dev,ino,fp,ext,error,a.regole,a.sha256,"
            "EXISTS(SELECT 1 FROM pages p WHERE p.doc_id=d.id),"
            "(SELECT ocr_checked FROM container_state WHERE doc_id=d.id),d.struttura,"
            "(SELECT min(n) FROM pages WHERE doc_id=d.id AND lettura IN ('pending','short')),d.ocr_lang "
            "FROM docs d LEFT JOIN analisi a ON a.doc_id=d.id" +
            (" WHERE d.path=?" if path else ""), (path,) if path else ())}

    def _skip_folder(self, path) -> bool:
        if not self.excluded_dirs:
            return False
        key = PathKey.of(path)
        return any(PathKey.within(key, d) for d in self.excluded_dirs)

    def _queue_file(self, path, fn, dirpath, ext, stat) -> None:
        self.todo.append((path, fn, dirpath, ext, stat))
        old = self.known.get(path, {})
        if self.ocr_on and stat.st_size <= self.max_bytes and (ext in IMAGE_EXTS or ext == "pdf" and (
                self.force_ocr or old.get("blank_pages") or old.get("notext"))):
            self.ocr_queue.add(path)

    # ---- fase 1: scansione
    def _scan(self) -> bool:
        """False se l'utente ha fermato la scansione."""
        st, known, sources = self.st, self.known, self.sources
        # Un intervento dalla diagnostica non scansiona le cartelle né elimina altri file.
        if self.target:
            target = self.target
            if target not in known or PathKey.of(target) in self.excluded_files or self._skip_folder(target):
                raise ValueError(tr("Seleziona un file già presente nell’archivio."))
            stat = os.stat(target)
            self._queue_file(target, os.path.basename(target), os.path.dirname(target), known[target]["ext"], stat)
            self.seen.add(target)
            st["scanned"] = 1
        for entry in ([] if self.target else self.folders):
            root = Path(entry["path"]).expanduser()
            recursive = entry.get("recursive", True)
            if sources.unavailable(root):
                continue
            self.seen_dirs[str(root)] = None  # le radici prevalgono su eventuali sotto-cartelle di altre radici
            for dirpath, dirnames, filenames in os.walk(root, onerror=sources.scan_error):
                sources.visit(dirpath)
                if self._stopped():
                    return False
                if recursive:
                    dirnames[:] = [d for d in dirnames if not d.startswith(".") and not self.excl.skip_dir(d)
                                   and not self._skip_folder(os.path.join(dirpath, d))
                                   and not sources.unavailable(os.path.join(dirpath, d))]
                    for d in dirnames:
                        self.seen_dirs.setdefault(os.path.join(dirpath, d), dirpath)
                else:
                    dirnames[:] = []
                for n, fn in enumerate(filenames):
                    if n % 128 == 0:
                        if self._stopped():
                            return False
                        self._report()
                    self._visit_file(dirpath, fn)
        if sources:
            sources.recheck()
            st["missing"] = sorted(sources.missing | sources.unreadable)
            if not self.dry_run:
                sources.save(self.con)
                self.con.commit()  # i distacchi restano registrati anche se il successivo OCR viene interrotto
        return True

    def _visit_file(self, dirpath: str, fn: str) -> None:
        if fn.startswith((".", "~$")) or self.excl.skip_file(fn):
            return
        ext = os.path.splitext(fn)[1].lower().lstrip(".")
        if ext not in self.exts:
            return
        path = os.path.join(dirpath, fn)
        if path in self.seen or (self.excluded_files and PathKey.of(path) in self.excluded_files):
            return  # cartelle sovrapposte
        try:
            stt = os.stat(path)
        except OSError as exc:
            self.sources.scan_error(exc)
            return
        self.seen.add(path)
        self.st["scanned"] += 1
        k = self.known.get(path)
        if k and k["mtime"] == stt.st_mtime and k["size"] == stt.st_size and not self._needs_rereading(k, ext, stt):
            if k["regole"] and not k["sha256"] and not k["error"] and stt.st_size <= self.max_bytes:
                self.reconnected.add(k["id"])
            return
        self._queue_file(path, fn, dirpath, ext, stt)
        self._report()

    def _needs_rereading(self, k: dict, ext: str, stt) -> bool:
        """File invariato che va riletto comunque: OCR appena attivato su PDF senza testo o con pagine
        senza testo (copertina digitale + scansioni), retry esplicito degli errori, o dipendenza mancante
        ora installata."""
        cfg, ocr_on, max_bytes = self.cfg, self.ocr_on, self.max_bytes
        probe = DependencyProbe
        retry_ocr = ocr_on and ext == "pdf" and (k["notext"] or k["blank_pages"]) and not k["ocr"]
        if k["next_ocr"] is not None:
            retry_ocr = ocr_on and k["next_ocr"] <= max(1, int(cfg.get("ocr_max_pages", 50) or 50))
        elif k["struttura"] and k["has_pages"]:
            retry_ocr = False
        page_error = ReadProblems.is_ocr_page_error(k["error"])
        retry_ocr = retry_ocr and (not k["error"] or page_error)
        # i controlli sulle dipendenze (import di pypdf, ricerca di pdftoppm) solo se serve
        retry_err = bool(k["error"]) and (
            self.retry_errors or (not page_error and probe.should_retry_error(k["error"], ocr_on)))
        # Nei vecchi indici questi formati non conservavano i dati strutturati.
        structured = (not k["sha256"] and ext in ("eml", "p7m", "xml") and not k["error"]
                      and not (k["ocr"] and not ocr_on))
        missing_pages = (ext == "pdf" and not k["has_pages"] and not k["notext"] and not k["error"]
                         and stt.st_size <= max_bytes and (not k["ocr"] or ocr_on) and probe.pypdf_available())
        upgrade = (not k["struttura"] and ext in ("xlsx", "pptx", "docx", "pdf", "p7m", "html", "htm", "odt", "rtf")
                   and not k["error"] and (not k["ocr"] or ocr_on) and stt.st_size <= max_bytes
                   and (ext != "pdf" or probe.pypdf_available()))
        retry_container = ext in ("eml", "zip") and (
            k["container_ocr"] is None or (ocr_on and not k["container_ocr"]))
        return bool(retry_ocr or retry_err or structured or missing_pages or retry_container or upgrade)

    # ---- rielaborazione OCR dopo un cambio di lingue
    def _set_pending_ocr(self, language: str) -> None:
        self.con.execute("UPDATE stato SET rielabora_ocr=? WHERE id=1", (language,))

    def _pending_ocr_language(self) -> str:
        """Lingue della rielaborazione OCR richiesta e non ancora completata; vuoto se non ce n'è
        una valida (una richiesta superata da un cambio successivo delle lingue viene tolta)."""
        row = self.con.execute("SELECT rielabora_ocr FROM stato WHERE id=1").fetchone()
        pending = row[0] if row else ""
        if pending and pending != OcrLanguages.normalize(self.cfg.get("ocr_lang")):
            if not self.dry_run:
                self._set_pending_ocr("")
                self.con.commit()
            return ""
        return pending

    def _reocr_candidates(self) -> tuple[list[tuple], int]:
        """Documenti da rileggere con l'OCR nelle lingue attuali e numero di quelli ora irraggiungibili.

        Solo documenti già letti con OCR in lingue diverse (o sconosciute): Word, Excel e PDF digitali non entrano."""
        if self.target or not self.ocr_on:
            return [], 0
        language = self._pending_ocr_language()
        if not language:
            return [], 0
        items, offline = [], 0
        rows = self.con.execute("SELECT path,name,folder,ext FROM docs WHERE ocr=1 AND ocr_lang<>? ORDER BY path",
                                (language,)).fetchall()
        for path, name, folder, ext in rows:
            if ext not in self.exts or PathKey.of(path) in self.excluded_files or self._skip_folder(path):
                continue
            if self.sources and self.sources.unavailable(path):
                offline += 1  # il disco potrebbe tornare: la richiesta resta in sospeso
                continue
            try:
                stt = os.stat(path)
            except OSError:
                continue  # sparito: lo toglie la normale pulizia
            if stt.st_size <= self.max_bytes:
                items.append((path, name, folder, ext, stt))
        return items, offline

    def _reprocess_ocr(self) -> None:
        """Rilegge con le lingue attuali i documenti già letti con OCR in altre lingue (su richiesta dell'utente).

        I risultati precedenti restano nell'archivio finché la nuova lettura non riesce, documento per documento.
        Se si interrompe, la richiesta resta in sospeso e riprende alla prossima esecuzione."""
        if self.target or not self.ocr_on or not self._pending_ocr_language():
            return  # con l'OCR spento la richiesta resta in attesa
        items, offline = self._reocr_candidates()
        if items:
            for path, *_ in items:
                self.known.update(self._load_known(path))
            self.reocr, self.todo, self.ocr_queue = True, items, {item[0] for item in items}
            self.st["done"] = 0
            try:
                if not self._process():
                    return
            finally:
                self.reocr = False
            self.con.commit()
        if not offline:
            self._set_pending_ocr("")
        self.con.commit()
        self.st["current"], self.st["phase"] = "", "done"

    def _find_orphans(self) -> None:
        """I documenti di un volume assente non sono candidati al riconoscimento delle rinomine."""
        sources = self.sources
        self.unseen = [p for p in self.known if p not in self.seen]
        self.orphans = {p: self.known[p] for p in self.unseen if not sources or not sources.unavailable(p)}
        self.by_ino = {(k["dev"], k["ino"]): p for p, k in self.orphans.items() if k["ino"]}
        self.by_size: dict[int, list[str]] = {}
        for p, k in self.orphans.items():
            self.by_size.setdefault(k["size"], []).append(p)

    def _count_changes(self) -> None:
        st, sources = self.st, self.sources
        expired = sum(1 for p in self.unseen if sources and sources.expired(p))
        st["changes"] = (len(self.todo) + len(self.orphans) + expired + len(self.reconnected)
                         + sum(k["regole"] != self.policy for k in self.known.values())
                         + len(self._reocr_candidates()[0]))
        if sources and not st["changes"]:
            maintenance = self.pending_vectors or sources.before != sources.after
            if not maintenance and sources.missing - sources.retained:
                paths = self.con.execute("SELECT path FROM dirs UNION ALL SELECT path FROM origini_cache_ocr")
                maintenance = any(sources.expired(p) for p, in paths)
            st["changes"] = int(maintenance)

    # ---- fase 2: elaborazione
    def _wait_for_power(self) -> bool:
        """A batteria si aspetta: la lettura riprende da sola con l'alimentatore. False se fermata."""
        st, stop = self.st, self.stop
        st["paused"] = True
        while PowerMonitor.on_battery() and not (stop is not None and stop.is_set()):
            self._report(True)
            if stop is not None:
                stop.wait(self.POWER_POLL_SECONDS)
            else:
                time.sleep(self.POWER_POLL_SECONDS)
        st["paused"] = False
        return not self._stopped()

    def _find_moved_source(self, path, stt, ext) -> str | None:
        """Se il file è una rinomina/spostamento di un documento già noto, ne restituisce il vecchio percorso."""
        orphans = self.orphans
        cand = self.by_ino.get((stt.st_dev, stt.st_ino)) if stt.st_ino else None
        if (cand in orphans and orphans[cand]["size"] == stt.st_size
                and orphans[cand]["mtime"] == stt.st_mtime and orphans[cand]["ext"] == ext):
            return cand
        # nessuna corrispondenza per inode (es. copia su un altro disco): impronta del contenuto
        same = [p for p in self.by_size.get(stt.st_size, [])
                if p in orphans and orphans[p]["mtime"] == stt.st_mtime and orphans[p]["ext"] == ext]
        if same:
            try:
                fp = FileFingerprint.of(path, stt.st_size)
                return next((p for p in same if orphans[p]["fp"] == fp), None)
            except OSError:
                pass
        return None

    def _process(self) -> bool:
        """False se fermata dall'utente."""
        st, con, cfg = self.st, self.con, self.cfg
        st["phase"] = "index"
        st["total"] = len(self.todo)
        self._pending, self._t_commit = 0, time.monotonic()
        # Il computer resta reattivo: dopo il lavoro in Python (non l'attesa di Tesseract, che gira già
        # a bassa priorità) ci si ferma per una parte di quel tempo. 0 = alla massima velocità.
        idle_ratio = max(0.0, min(2.0, float(cfg.get("cpu_throttle", 0.5) or 0)))
        cpu_mark, idle_debt = time.thread_time(), 0.0
        pause_on_battery = bool(cfg.get("pause_on_battery"))
        if self.progress and self.ocr_on:
            self.cfg = cfg = dict(cfg, _ocr_started=self._discovered_ocr)
        for path, fn, dirpath, ext, stt in self.todo:
            self._current_path = path
            if self._stopped():
                return False
            if pause_on_battery and PowerMonitor.on_battery() and not self._wait_for_power():
                return False
            if idle_ratio:
                now_cpu = time.thread_time()
                idle_debt += (now_cpu - cpu_mark) * idle_ratio
                if idle_debt >= 0.02:
                    time.sleep(min(idle_debt, 1.0))  # libera GIL e CPU a interfaccia e altri programmi
                    idle_debt = 0.0
                cpu_mark = time.thread_time()
            st["current"] = short_path(path)
            # file lenti (PDF/immagini/grandi): mostra subito il nome; i piccoli al massimo 10 volte al secondo
            self._report(ext == "pdf" or ext in IMAGE_EXTS or stt.st_size > 1_000_000)
            src = self._find_moved_source(path, stt, ext) if path not in self.known and self.orphans else None
            if src:
                self.store.move(self.orphans.pop(src)["id"], path, fn, dirpath, stt)
                st["moved"] += 1
            else:
                outcome = self._read_and_store(path, fn, dirpath, ext, stt)
                if outcome == "stopped":
                    return False
                if outcome == "skipped":
                    self.ocr_queue.discard(path)
                    st["done"] += 1
                    continue
            self.ocr_queue.discard(path)
            st["done"] += 1
            self._pending += 1
            if self._pending >= 50 or time.monotonic() - self._t_commit > 2:
                con.commit()
                self._pending, self._t_commit = 0, time.monotonic()
        return True

    def _discovered_ocr(self) -> None:
        if self._current_path not in self.ocr_queue:
            self.ocr_queue.add(self._current_path)
            self._report(True)

    def _read_and_store(self, path, fn, dirpath, ext, stt) -> str:
        """Legge un file e lo salva. Restituisce «stored», «skipped» (da non contare come scritto) o «stopped»."""
        con, cfg, st, known, target = self.con, self.cfg, self.st, self.known, self.target
        content, ocr_used, fp, err_msg = "", False, None, ""
        info: dict = {"container_ocr": self.ocr_on}
        cache = None
        if stt.st_size <= self.max_bytes:
            try:
                if self.ocr_on and ext == "pdf" and not self.force_ocr:
                    fp = FileFingerprint.of(path, stt.st_size)
                    cache = OcrCache(con, fp, OcrLanguages.normalize(cfg.get("ocr_lang")), path)
                old = known.get(path)
                if (cache and old and old["ocr"] and old["mtime"] == stt.st_mtime
                        and old["size"] == stt.st_size and old["fp"] == fp and not target):
                    rows = con.execute(
                        """SELECT n, CASE WHEN lettura='ocr' THEN substr(content,start+1,
                           coalesce((SELECT q.start-1 FROM pages q WHERE q.doc_id=p.doc_id AND q.n=p.n+1),
                                    length(content))-start) ELSE '' END, lingua
                           FROM pages p JOIN fts ON fts.rowid=p.doc_id
                           WHERE doc_id=? AND lettura IN ('ocr','ocr_empty','native_ocr_empty')""",
                        (old["id"],)).fetchall()
                    # Nella rielaborazione il testo precedente non sostituisce la nuova lettura: serve solo
                    # a non perdere una pagina che non si riesce a rileggere.
                    kind = "fallback_ocr" if self.reocr else "previous_ocr"
                    info[kind] = {n: text for n, text, _ in rows}
                    info[kind + "_lang"] = {n: lang for n, _, lang in rows}
                content, ocr_used = registry.extract(path, ext, cfg, stop=self.stop, cache=cache, info=info)
                content = content[:self.max_chars]
                if ocr_used:
                    langs = info.get("ocr_langs")
                    if langs is None:
                        langs = {OcrLanguages.normalize(cfg.get("ocr_lang"))}
                    info["ocr_lang"] = "|".join(sorted(langs))
            except Stopped:
                con.commit()
                st["stopped"] = True
                return "stopped"
            except Exception as ex:
                err_msg = str(ex).strip()[:300] or type(ex).__name__
                st["errors"] += 1  # resta cercabile almeno per nome
        else:
            err_msg = f"File oltre il limite ({human_size(stt.st_size)} > {cfg.get('max_file_mb', 50)} MB)"
            st["errors"] += 1
        if self.reocr:
            problem = err_msg or ("" if ext == "pdf" else info.get("partial_error", ""))
            if problem:
                # La lettura precedente resta valida e ricercabile: si segnala soltanto che questa non è riuscita.
                if not err_msg:
                    st["errors"] += 1
                con.execute("UPDATE docs SET error=? WHERE path=?",
                            (problem if ReadProblems.is_partial(problem) else ReadProblems.PARTIAL + problem, path))
                return "skipped"
        if (err_msg and not target and path in known and known[path]["mtime"] == stt.st_mtime
                and known[path]["size"] == stt.st_size and (not known[path]["struttura"] or ext == "pdf")):
            con.execute("UPDATE docs SET error=? WHERE path=?", (ReadProblems.PARTIAL + err_msg, path))
            return "skipped"
        if target and self.force_ocr and info.get("partial_error"):
            raise RuntimeError(info["partial_error"])
        if target and err_msg:
            with con:
                con.execute("UPDATE docs SET error=? WHERE path=?", (err_msg, target))
            raise RuntimeError(err_msg)
        if info.get("partial_error"):
            err_msg = info["partial_error"]
            st["errors"] += 1
        try:
            fp = FileFingerprint.of(path, stt.st_size)
            if stt.st_size <= self.max_bytes:
                info["sha256"] = FileHasher.sha256(path)
        except OSError:
            # Un supporto può essere stato rimosso dopo os.walk. Conserva l'ultima lettura riuscita.
            if self.sources:
                self.sources.recheck()
                if self.sources.unavailable(path):
                    return "skipped"
        st[self.store.save(path, fn, dirpath, ext, stt, content, ocr_used, fp, err_msg,
                           info.get("blank_pages", 0), info.get("meta", ""), info.get("pages", ()), info)] += 1
        if cache:
            cache.clear()  # file completato: le pagine temporanee non servono più
        return "stored"

    # ---- file spariti e ordine finale
    def _cleanup(self) -> None:
        con, st, sources = self.con, self.st, self.sources
        sources.recheck()
        sources.save(con)
        st["missing"] = sorted(sources.missing | sources.unreadable)
        for path, k in self.orphans.items():
            if sources.unavailable(path):
                continue
            self.store.delete(k["id"])
            st["removed"] += 1
        if sources.missing - sources.retained:
            expired = [doc_id for path, doc_id in con.execute("SELECT path,id FROM docs")
                       if path not in self.seen and sources.expired(path)]
            st["removed"] += ArchiveEraser(con).erase_documents(expired)
            ArchiveEraser(con).erase_cached_paths(sources.expired)
            con.commit()
            ArchiveEraser(con).finish_vector_removal()
        # Conserviamo testo, pagine e vettori già validi durante una migrazione o un cambio di preferenze.
        current_rules = con.execute("SELECT valore FROM regole_grafo WHERE id=1").fetchone()[0]
        backlog = con.execute("SELECT d.id FROM docs d LEFT JOIN analisi a ON a.doc_id=d.id "
                              "WHERE a.doc_id IS NULL OR a.regole<>?", (current_rules,)).fetchall()
        t_commit = self._t_commit
        for doc_id in sorted(self.reconnected.union(row[0] for row in backlog)):
            if self._stopped():
                return
            self.store.backfill(doc_id, self.cfg)
            if time.monotonic() - t_commit > 1:
                con.commit()
                t_commit = time.monotonic()
        # ---- struttura delle cartelle
        old_dirs = {r[0] for r in con.execute("SELECT path FROM dirs")}
        for d, parent in self.seen_dirs.items():
            con.execute("INSERT OR REPLACE INTO dirs(path,parent) VALUES(?,?)", (d, parent))
        for d in old_dirs - set(self.seen_dirs):
            if not sources.unavailable(d) or sources.expired(d):
                con.execute("DELETE FROM dirs WHERE path=?", (d,))
        con.commit()
        st["current"], st["phase"] = "", "done"
