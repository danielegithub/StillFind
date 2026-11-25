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

from . import archive, background, private_tmp, relations
from .sources import Sources
from .db import connect
from .duplicates import sha256_file
from .errors import Stopped
from .extractors import IMAGE_EXTS, extract, ocr
from .extractors.ocr import _OcrCache
from .stemmer import stem_text
from .system import human_size, short_path


def fingerprint(path: str, size: int) -> str:
    """Impronta del contenuto (dimensione + primi e ultimi 64 KB): riconosce lo stesso
    file anche dopo rinomina o spostamento, senza rileggerlo tutto."""
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(65536))
        if size > 131072:
            f.seek(-65536, os.SEEK_END)
            h.update(f.read(65536))
    return h.hexdigest()


def _store(con, path, name, folder, ext, st_, content, ocr, fp, error: str = "",
           blank_pages: int = 0, meta: str = "", pages=(), info=None) -> str:
    notext = int(not content.strip())  # i metadati non contano: un PDF scansionato con titolo resta «senza testo»
    if meta:
        content = meta + "\n\n" + content
    vals = (name, folder, ext, st_.st_size, st_.st_mtime, notext, int(ocr), blank_pages,
            st_.st_dev, st_.st_ino, fp, error)
    if not con.in_transaction:
        con.execute("BEGIN IMMEDIATE")
    existing = con.execute("SELECT id FROM docs WHERE path=?", (path,)).fetchone()
    if existing:
        doc_id = existing[0]
        con.execute("UPDATE docs SET name=?, folder=?, ext=?, size=?, mtime=?, notext=?, ocr=?, "
                    "blank_pages=?, dev=?, ino=?, fp=?, error=? WHERE id=?", vals + (doc_id,))
        con.execute("DELETE FROM fts WHERE rowid=?", (doc_id,))
        con.execute("DELETE FROM vec_done WHERE doc_id=?", (doc_id,))  # testo cambiato: vettori da ricalcolare
        con.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
        kind = "updated"
    else:
        doc_id = con.execute(
            "INSERT INTO docs(path,name,folder,ext,size,mtime,notext,ocr,blank_pages,dev,ino,fp,error) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (path,) + vals).lastrowid
        kind = "added"
    # radici separate (contenuto / nome): servono a «Cerca in: Nel contenuto» e «Nel nome»
    con.execute("INSERT INTO fts(rowid,name,content,stem,nstem) VALUES(?,?,?,?,?)",
                (doc_id, name, content, stem_text(content), stem_text(name)))
    shift = len(meta) + 2 if meta else 0
    sections = (info or {}).get("sections", [(s, "page", str(n), "") for n, s in enumerate(pages, 1)])
    rows = [(doc_id, n, s + shift, kind_, title, state)
            for n, (s, kind_, title, state) in enumerate(sections, 1) if s + shift <= len(content)]
    con.executemany("INSERT INTO pages(doc_id,n,start,tipo,titolo,lettura) VALUES(?,?,?,?,?,?)", rows)
    con.execute("UPDATE docs SET struttura=1 WHERE id=?", (doc_id,))
    starts = [(n if typ == "page" else None, s) for _, n, s, typ, _, _ in rows]
    policy = con.execute("SELECT valore FROM regole_grafo WHERE id=1").fetchone()[0]
    relations.index_document(con, doc_id, content, starts, ext, info or {}, policy, shift)
    if ext in ("eml", "zip"):
        con.execute("INSERT OR REPLACE INTO container_state VALUES(?,?)",
                    (doc_id, int((info or {}).get("container_ocr", False))))
    return kind


def _backfill(con, doc_id, cfg):
    """Completa dal testo conservato anche le schede di un disco scollegato, senza rifare l'OCR."""
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
        from .extractors.meta import read_metadata
        try:
            st = os.stat(path)
            if st.st_size == size and st.st_mtime == mtime:
                info["sha256"] = sha256_file(path)
                meta = read_metadata(path, ext, info)
                if meta and content.startswith(meta + "\n\n"):
                    shift = len(meta) + 2
        except OSError:
            pass
    pages = list(con.execute("SELECT CASE WHEN tipo='page' THEN n END,start FROM pages WHERE doc_id=? ORDER BY n", (doc_id,)))
    policy = con.execute("SELECT valore FROM regole_grafo WHERE id=1").fetchone()[0]
    relations.index_document(con, doc_id, content, pages, ext, info, policy, shift or 0)


def _move(con, doc_id, path, name, folder, st_) -> None:
    """Stesso file con nome/posizione diversi: aggiorna solo i dati, senza rileggerlo."""
    con.execute("UPDATE docs SET path=?, name=?, folder=?, mtime=?, dev=?, ino=? WHERE id=?",
                (path, name, folder, st_.st_mtime, st_.st_dev, st_.st_ino, doc_id))
    con.execute("UPDATE fts SET name=?, nstem=? WHERE rowid=?", (name, stem_text(name), doc_id))


# pausa tra un controllo e l'altro della batteria (secondi); si abbassa nei test
POWER_POLL_SECONDS = 2.0


_FOUND: set[str] = set()  # dipendenze già trovate (un «no» non si ricorda: potrebbero installarla mentre l'app è aperta)


def _pypdf_available() -> bool:
    """pypdf è installato? Si guarda senza importarlo (importarlo costa RAM, e l'agente non deve farlo)."""
    if "pypdf" in _FOUND:
        return True
    if importlib.util.find_spec("pypdf") is None:
        return False
    _FOUND.add("pypdf")
    return True


def _pdf_render_available() -> bool:
    """Esiste un modo per disegnare le pagine dei PDF per l'OCR (pdftoppm o PyMuPDF)?"""
    if "render" in _FOUND:
        return True
    found = ocr.which("pdftoppm") is not None or importlib.util.find_spec("pypdfium2") is not None
    if not found:
        try:
            import fitz  # noqa: F401
            found = True
        except ImportError:
            pass
    if found:
        _FOUND.add("render")
    return found


def _should_retry_error(err: str, ocr_on: bool, has_pypdf, has_pdf_render) -> bool:
    """`has_pypdf` e `has_pdf_render` sono funzioni: si chiamano solo se l'errore le riguarda."""
    if not err:
        return False
    low = err.lower()
    if "pypdf" in low and has_pypdf():
        return True
    if ocr_on and ("tesseract" in low or ("pdftoppm" in low and has_pdf_render())):
        return True
    return False


def index_all(cfg: dict, progress=None, stop: threading.Event | None = None,
              retry_errors: bool = False, dry_run: bool = False, target: str | None = None,
              force_ocr: bool = False, *, con=None) -> dict:
    """Indicizzazione incrementale.

    Con `dry_run=True` si ferma dopo la fase 1 senza leggere né scrivere nulla e riporta in `changes`
    quanti file sono da leggere o da togliere: serve all'agente per decidere se avviare il lavoratore.

    Fase 1: scorre le cartelle (con o senza sottocartelle) e costruisce l'albero.
    Fase 2: legge solo i file nuovi/modificati; un file rinominato o spostato (anche
            tra cartelle indicizzate diverse) viene riconosciuto e non riletto.
    Poi toglie dall'indice i file davvero spariti.
    """
    if con is None:
        with closing(connect()) as con:
            return _index_all(con, cfg, progress, stop, retry_errors, dry_run, target, force_ocr)
    return _index_all(con, cfg, progress, stop, retry_errors, dry_run, target, force_ocr)


def _index_all(con, cfg, progress, stop, retry_errors, dry_run, target, force_ocr):
    if force_ocr:
        if not target or Path(target).suffix.lower().lstrip(".") not in IMAGE_EXTS | {"pdf"}:
            raise ValueError("Scegli un PDF o un’immagine per l’OCR.")
        if not ocr.ocr_available():
            raise RuntimeError("Tesseract non disponibile.")
        cfg = dict(cfg, ocr=True, _force_ocr=True)
    exts = {e.lower().lstrip(".") for e in cfg["extensions"]}
    ocr_on = bool(cfg.get("ocr")) and ocr.ocr_available()
    if ocr_on:
        exts |= IMAGE_EXTS
    excl = set(cfg["exclude_dirs"])
    max_bytes = max(1, int(cfg.get("max_file_mb", 50) or 50)) * 1024 * 1024
    max_chars = cfg["max_chars_per_file"]
    policy = relations.rules(cfg)
    excluded = list(con.execute("SELECT path, cartella FROM esclusioni"))
    excluded_files = {p for p, folder in excluded if not folder}
    excluded_dirs = {p for p, folder in excluded if folder}

    def skip_folder(path):
        if not excluded_dirs:
            return False
        key = archive.path_key(path)
        return any(archive.within(key, d) for d in excluded_dirs)

    private_root = archive.path_key(os.path.realpath(private_tmp.root_path()))
    folders = [f for f in cfg["folders"] if not skip_folder(f["path"])
               and not archive.within(archive.path_key(os.path.realpath(os.path.expanduser(f["path"]))), private_root)]
    sources = None if target else Sources(con, folders, cfg.get("offline_retention_days", 0))
    pending_vectors = bool(con.execute("SELECT 1 FROM vettori_da_togliere LIMIT 1").fetchone())
    if not dry_run and pending_vectors:
        archive.finish_vector_removal(con)
    if not dry_run:
        relations.set_rules(con, cfg)
        if sources:
            sources.save(con)
        con.commit()
    cols = ("id", "path", "mtime", "size", "notext", "ocr", "blank_pages", "dev", "ino", "fp", "ext", "error",
            "regole", "sha256", "has_pages", "container_ocr", "struttura", "next_ocr")
    known = {r[1]: dict(zip(cols, r)) for r in
             con.execute("SELECT d.id,path,mtime,size,notext,ocr,blank_pages,dev,ino,fp,ext,error,a.regole,a.sha256,"
                         "EXISTS(SELECT 1 FROM pages p WHERE p.doc_id=d.id),"
                         "(SELECT ocr_checked FROM container_state WHERE doc_id=d.id),d.struttura,"
                         "(SELECT min(n) FROM pages WHERE doc_id=d.id AND lettura IN ('pending','short')) "
                         "FROM docs d LEFT JOIN analisi a ON a.doc_id=d.id" +
                         (" WHERE d.path=?" if target else ""), (target,) if target else ())}
    seen: set[str] = set()
    seen_dirs: dict[str, str | None] = {}
    todo: list[tuple] = []
    reconnected = set()
    st = {"scanned": 0, "added": 0, "updated": 0, "moved": 0, "removed": 0, "errors": 0,
          "missing": [], "current": "", "phase": "scan", "total": 0, "done": 0, "paused": False}
    last = [0.0]

    def report(force=False):
        now = time.monotonic()
        if progress and (force or now - last[0] > 0.1):
            last[0] = now
            st["ocr_pending"] = len(ocr_queue)
            progress(dict(st))

    def stopped() -> bool:
        if stop is not None and stop.is_set():
            con.commit()
            st["stopped"] = True
            return True
        return False

    ocr_queue = set()

    def queue_file(path, fn, dirpath, ext, stat):
        todo.append((path, fn, dirpath, ext, stat))
        old = known.get(path, {})
        if ocr_on and stat.st_size <= max_bytes and (ext in IMAGE_EXTS or
                ext == "pdf" and (force_ocr or old.get("blank_pages") or old.get("notext"))):
            ocr_queue.add(path)

    # Un intervento dalla diagnostica non scansiona le cartelle né elimina altri file.
    if target:
        if target not in known or archive.path_key(target) in excluded_files or skip_folder(target):
            raise ValueError("Seleziona un file già presente nell’archivio.")
        stat = os.stat(target)
        queue_file(target, os.path.basename(target), os.path.dirname(target), known[target]["ext"], stat)
        seen.add(target)
        st["scanned"] = 1
    # ---- fase 1: scansione
    for entry in ([] if target else folders):
        root = Path(entry["path"]).expanduser()
        recursive = entry.get("recursive", True)
        if sources.unavailable(root):
            continue
        rp = str(root)
        seen_dirs[rp] = None  # le radici prevalgono su eventuali sotto-cartelle di altre radici
        for dirpath, dirnames, filenames in os.walk(root, onerror=sources.scan_error):
            sources.visit(dirpath)
            if stopped():
                return st
            if recursive:
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in excl
                               and not skip_folder(os.path.join(dirpath, d))
                               and not sources.unavailable(os.path.join(dirpath, d))]
                for d in dirnames:
                    seen_dirs.setdefault(os.path.join(dirpath, d), dirpath)
            else:
                dirnames[:] = []
            for n, fn in enumerate(filenames):
                if n % 128 == 0:
                    if stopped():
                        return st
                    report()
                if fn.startswith((".", "~$")):
                    continue
                ext = os.path.splitext(fn)[1].lower().lstrip(".")
                if ext not in exts:
                    continue
                path = os.path.join(dirpath, fn)
                if path in seen or (excluded_files and archive.path_key(path) in excluded_files):
                    continue  # cartelle sovrapposte
                try:
                    stt = os.stat(path)
                except OSError as exc:
                    sources.scan_error(exc)
                    continue
                seen.add(path)
                st["scanned"] += 1
                k = known.get(path)
                if k and k["mtime"] == stt.st_mtime and k["size"] == stt.st_size:
                    # invariato; eccezioni: OCR appena attivato su PDF senza testo o con pagine
                    # senza testo (copertina digitale + scansioni), retry esplicito degli errori,
                    # o dipendenza mancante ora installata
                    retry_ocr = (ocr_on and ext == "pdf" and (k["notext"] or k["blank_pages"])
                                 and not k["ocr"])
                    if k["next_ocr"] is not None:
                        retry_ocr = ocr_on and k["next_ocr"] <= max(1, int(cfg.get("ocr_max_pages", 50) or 50))
                    elif k["struttura"] and k["has_pages"]:
                        retry_ocr = False
                    page_error = (k["error"] or "").startswith("Lettura parziale · OCR,")
                    retry_ocr = retry_ocr and (not k["error"] or page_error)
                    retry_err = bool(k["error"]) and (
                        # i controlli sulle dipendenze (import di pypdf, ricerca di pdftoppm) solo se serve
                        retry_errors or (not page_error and _should_retry_error(k["error"], ocr_on, _pypdf_available,
                                                                                _pdf_render_available)))
                    # Nei vecchi indici questi formati non conservavano i dati strutturati.
                    structured = (not k["sha256"] and ext in ("eml", "p7m", "xml") and not k["error"]
                                  and not (k["ocr"] and not ocr_on))
                    missing_pages = (ext == "pdf" and not k["has_pages"] and not k["notext"] and not k["error"]
                                     and stt.st_size <= max_bytes and (not k["ocr"] or ocr_on) and _pypdf_available())
                    upgrade = (not k["struttura"] and ext in ("xlsx", "pptx", "docx", "pdf", "p7m", "html", "htm", "odt", "rtf")
                               and not k["error"] and (not k["ocr"] or ocr_on) and stt.st_size <= max_bytes
                               and (ext != "pdf" or _pypdf_available()))
                    retry_container = ext in ("eml", "zip") and (
                        k["container_ocr"] is None or (ocr_on and not k["container_ocr"]))
                    if not (retry_ocr or retry_err or structured or missing_pages or retry_container or upgrade):
                        if k["regole"] and not k["sha256"] and not k["error"] and stt.st_size <= max_bytes:
                            reconnected.add(k["id"])
                        continue
                queue_file(path, fn, dirpath, ext, stt)
                report()

    if sources:
        sources.recheck()
        st["missing"] = sorted(sources.missing | sources.unreadable)
        if not dry_run:
            sources.save(con)
            con.commit()  # i distacchi restano registrati anche se il successivo OCR viene interrotto

    # I documenti di un volume assente non sono candidati al riconoscimento delle rinomine.
    unseen = [p for p in known if p not in seen]
    orphans = {p: known[p] for p in unseen if not sources or not sources.unavailable(p)}
    by_ino = {(k["dev"], k["ino"]): p for p, k in orphans.items() if k["ino"]}
    by_size: dict[int, list[str]] = {}
    for p, k in orphans.items():
        by_size.setdefault(k["size"], []).append(p)

    if dry_run:
        expired = sum(1 for p in unseen if sources and sources.expired(p))
        st["changes"] = (len(todo) + len(orphans) + expired + len(reconnected)
                         + sum(k["regole"] != policy for k in known.values()))
        if sources and not st["changes"]:
            maintenance = pending_vectors or sources.before != sources.after
            if not maintenance and sources.missing - sources.retained:
                paths = con.execute("SELECT path FROM dirs UNION ALL SELECT path FROM origini_cache_ocr")
                maintenance = any(sources.expired(p) for p, in paths)
            st["changes"] = int(maintenance)
        return st

    # ---- fase 2: elaborazione
    st["phase"] = "index"
    st["total"] = len(todo)
    pending, t_commit = 0, time.monotonic()
    # Il computer resta reattivo: dopo il lavoro in Python (non l'attesa di Tesseract, che gira già
    # a bassa priorità) ci si ferma per una parte di quel tempo. 0 = alla massima velocità.
    idle_ratio = max(0.0, min(2.0, float(cfg.get("cpu_throttle", 0.5) or 0)))
    cpu_mark, idle_debt = time.thread_time(), 0.0
    pause_on_battery = bool(cfg.get("pause_on_battery"))

    def discovered_ocr():
        if path not in ocr_queue:
            ocr_queue.add(path)
            report(True)

    if progress and ocr_on:
        cfg = dict(cfg, _ocr_started=discovered_ocr)
    for path, fn, dirpath, ext, stt in todo:
        if stopped():
            return st
        if pause_on_battery and background.on_battery():
            st["paused"] = True  # a batteria si aspetta: la lettura riprende da sola quando si collega l'alimentatore
            while background.on_battery() and not (stop is not None and stop.is_set()):
                report(True)
                if stop is not None:
                    stop.wait(POWER_POLL_SECONDS)
                else:
                    time.sleep(POWER_POLL_SECONDS)
            st["paused"] = False
            if stopped():
                return st
        if idle_ratio:
            now_cpu = time.thread_time()
            idle_debt += (now_cpu - cpu_mark) * idle_ratio
            if idle_debt >= 0.02:
                time.sleep(min(idle_debt, 1.0))  # libera GIL e CPU a interfaccia e altri programmi
                idle_debt = 0.0
            cpu_mark = time.thread_time()
        st["current"] = short_path(path)
        # file lenti (PDF/immagini/grandi): mostra subito il nome; i piccoli al massimo 10 volte al secondo
        report(ext == "pdf" or ext in IMAGE_EXTS or stt.st_size > 1_000_000)
        src = None
        if path not in known and orphans:
            cand = by_ino.get((stt.st_dev, stt.st_ino)) if stt.st_ino else None
            if (cand in orphans and orphans[cand]["size"] == stt.st_size
                    and orphans[cand]["mtime"] == stt.st_mtime and orphans[cand]["ext"] == ext):
                src = cand
            else:  # nessuna corrispondenza per inode (es. copia su un altro disco): impronta del contenuto
                same = [p for p in by_size.get(stt.st_size, [])
                        if p in orphans and orphans[p]["mtime"] == stt.st_mtime and orphans[p]["ext"] == ext]
                if same:
                    try:
                        fp = fingerprint(path, stt.st_size)
                        src = next((p for p in same if orphans[p]["fp"] == fp), None)
                    except OSError:
                        pass
        if src:
            _move(con, orphans.pop(src)["id"], path, fn, dirpath, stt)
            st["moved"] += 1
        else:
            content, ocr_used, fp, err_msg = "", False, None, ""
            info: dict = {"container_ocr": ocr_on}
            cache = None
            if stt.st_size <= max_bytes:
                try:
                    if ocr_on and ext == "pdf" and not force_ocr:
                        fp = fingerprint(path, stt.st_size)
                        cache = _OcrCache(con, fp, cfg.get("ocr_lang", "ita+eng"), path)
                    old = known.get(path)
                    if (cache and old and old["ocr"] and old["mtime"] == stt.st_mtime
                            and old["size"] == stt.st_size and old["fp"] == fp and not target):
                        info["previous_ocr"] = dict(con.execute(
                            """SELECT n, CASE WHEN lettura='ocr' THEN substr(content,start+1,
                               coalesce((SELECT q.start-1 FROM pages q WHERE q.doc_id=p.doc_id AND q.n=p.n+1),
                                        length(content))-start) ELSE '' END
                               FROM pages p JOIN fts ON fts.rowid=p.doc_id
                               WHERE doc_id=? AND lettura IN ('ocr','ocr_empty','native_ocr_empty')""", (old["id"],)))
                    content, ocr_used = extract(path, ext, cfg, stop=stop, cache=cache, info=info)
                    content = content[:max_chars]
                except Stopped:
                    con.commit()
                    st["stopped"] = True
                    return st
                except Exception as ex:
                    err_msg = str(ex).strip()[:300] or type(ex).__name__
                    st["errors"] += 1  # resta cercabile almeno per nome
            else:
                err_msg = f"File oltre il limite ({human_size(stt.st_size)} > {cfg.get('max_file_mb', 50)} MB)"
                st["errors"] += 1
            if (err_msg and not target and path in known and known[path]["mtime"] == stt.st_mtime
                    and known[path]["size"] == stt.st_size and (not known[path]["struttura"] or ext == "pdf")):
                con.execute("UPDATE docs SET error=? WHERE path=?", ("Lettura parziale · " + err_msg, path))
                ocr_queue.discard(path)
                st["done"] += 1
                continue
            if target and force_ocr and info.get("partial_error"):
                raise RuntimeError(info["partial_error"])
            if target and err_msg:
                with con:
                    con.execute("UPDATE docs SET error=? WHERE path=?", (err_msg, target))
                raise RuntimeError(err_msg)
            if info.get("partial_error"):
                err_msg = info["partial_error"]
                st["errors"] += 1
            try:
                fp = fingerprint(path, stt.st_size)
                if stt.st_size <= max_bytes:
                    info["sha256"] = sha256_file(path)
            except OSError:
                # Un supporto può essere stato rimosso dopo os.walk. Conserva l'ultima lettura riuscita.
                if sources:
                    sources.recheck()
                    if sources.unavailable(path):
                        ocr_queue.discard(path)
                        st["done"] += 1
                        continue
            st[_store(con, path, fn, dirpath, ext, stt, content, ocr_used, fp, err_msg,
                      info.get("blank_pages", 0), info.get("meta", ""), info.get("pages", ()), info)] += 1
            if cache:
                cache.clear()  # file completato: le pagine temporanee non servono più
        ocr_queue.discard(path)
        st["done"] += 1
        pending += 1
        if pending >= 50 or time.monotonic() - t_commit > 2:
            con.commit()
            pending, t_commit = 0, time.monotonic()

    if target:
        con.commit()
        st["current"], st["phase"] = "", "done"
        return st

    # ---- file spariti (cancellati o spostati fuori dalle cartelle indicizzate)
    sources.recheck()
    sources.save(con)
    st["missing"] = sorted(sources.missing | sources.unreadable)
    for path, k in orphans.items():
        if sources.unavailable(path):
            continue
        con.execute("DELETE FROM docs WHERE id=?", (k["id"],))
        con.execute("DELETE FROM fts WHERE rowid=?", (k["id"],))
        con.execute("DELETE FROM vec_done WHERE doc_id=?", (k["id"],))  # i vettori si tolgono al prossimo calcolo
        con.execute("DELETE FROM pages WHERE doc_id=?", (k["id"],))
        st["removed"] += 1
    if sources.missing - sources.retained:
        expired = [doc_id for path, doc_id in con.execute("SELECT path,id FROM docs")
                   if path not in seen and sources.expired(path)]
        st["removed"] += archive.erase_documents(con, expired)
        archive.erase_cached_paths(con, sources.expired)
        con.commit()
        archive.finish_vector_removal(con)
    # Conserviamo testo, pagine e vettori già validi durante una migrazione o un cambio di preferenze.
    current_rules = con.execute("SELECT valore FROM regole_grafo WHERE id=1").fetchone()[0]
    backlog = con.execute("SELECT d.id FROM docs d LEFT JOIN analisi a ON a.doc_id=d.id "
                          "WHERE a.doc_id IS NULL OR a.regole<>?", (current_rules,)).fetchall()
    for doc_id in sorted(reconnected.union(row[0] for row in backlog)):
        if stopped():
            return st
        _backfill(con, doc_id, cfg)
        if time.monotonic() - t_commit > 1:
            con.commit()
            t_commit = time.monotonic()
    # ---- struttura delle cartelle
    old_dirs = {r[0] for r in con.execute("SELECT path FROM dirs")}
    for d, parent in seen_dirs.items():
        con.execute("INSERT OR REPLACE INTO dirs(path,parent) VALUES(?,?)", (d, parent))
    for d in old_dirs - set(seen_dirs):
        if not sources.unavailable(d) or sources.expired(d):
            con.execute("DELETE FROM dirs WHERE path=?", (d,))
    con.commit()
    st["current"], st["phase"] = "", "done"
    return st
