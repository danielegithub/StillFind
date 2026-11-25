# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Ricerca per significato: passaggi, calcolo dei vettori (lavoratore) e ricerca ibrida (finestra, terminale).

Come funziona:
  * ogni documento è diviso in **passaggi** di circa 120 parole (con 20 di sovrapposizione); la tabella
    `vec_chunks` ricorda dove comincia e finisce ciascuno (posizioni nel testo dell'archivio), e il suo
    numero è anche l'identificativo del vettore in `vectors.VectorStore`;
  * `vec_done` segna i documenti già calcolati (con data e dimensione del file al momento del calcolo): un
    documento cambiato o riletto si ricalcola, uno cancellato si toglie;
  * i vettori li calcola **solo il lavoratore** (`embed_pending`), a priorità bassa e riprendendo da dove si
    era fermato; la ricerca (`hybrid_search`) carica il modello solo alla prima ricerca per significato;
  * i risultati per parole e per significato si uniscono con la *Reciprocal Rank Fusion*, con più peso alle
    parole: chi cerca un termine esatto lo trova comunque per primo.

Questo modulo non importa `numpy`, `onnxruntime` né `sentencepiece` finché non servono: l'agente lo usa solo
per sapere se c'è lavoro da fare."""
from __future__ import annotations

import importlib.util
import re
import time
from itertools import islice

from . import background, models, search as searcher
from .config import DB_PATH, DEFAULT_CONFIG
from .db import connect

PASSAGE_WORDS = 120
OVERLAP_WORDS = 20
MAX_PASSAGES = 300       # per documento: oltre, il resto del documento non si legge «per significato»
MIN_SCORE = DEFAULT_CONFIG["semantic_min_score"]
MARGIN = 0.015           # scarto dal migliore, tarato insieme alla soglia su 30 ricerche
SEMANTIC_HITS = 60       # documenti distinti, ognuno con il proprio passaggio migliore
RRF_K = 60               # costante della Reciprocal Rank Fusion
WEIGHT_WORDS, WEIGHT_MEANING = 1.0, 0.5
BATCH_STORE = 32         # passaggi calcolati e scritti per volta
SNIPPET_CHARS = 240
COMPACT_RATIO = 0.3      # si compatta l'archivio quando più del 30% delle righe è cancellato

_WORD = re.compile(r"\S+")

# righe dei passaggi con un calcolo valido: il documento esiste e non è cambiato dopo il calcolo
_VALID = ("FROM vec_chunks c JOIN vec_done v ON v.doc_id = c.doc_id "
          "JOIN docs d ON d.id = c.doc_id AND d.mtime = v.mtime AND d.size = v.size AND v.model = ?")


# ------------------------------------------------------------------ stato (solo SQL, nessuna libreria pesante)
def libraries_available() -> bool:
    """`onnxruntime` e `sentencepiece` sono installati? Si guarda senza importarli."""
    return all(importlib.util.find_spec(m) is not None for m in ("numpy", "onnxruntime", "sentencepiece"))


def available(cfg: dict, models_dir=None) -> bool:
    """La ricerca per significato è attiva nelle impostazioni e tutto il necessario è presente."""
    return bool(cfg.get("semantic_search")) and models.is_installed(models.DEFAULT_MODEL, models_dir) \
        and libraries_available()


def pending_count(con, model: str = models.DEFAULT_MODEL) -> int:
    """Documenti con i vettori da calcolare o da ricalcolare."""
    return con.execute(
        "SELECT COUNT(*) FROM docs d LEFT JOIN vec_done v ON v.doc_id = d.id "
        "WHERE v.doc_id IS NULL OR v.mtime != d.mtime OR v.size != d.size OR v.model != ?", (model,)).fetchone()[0]


def status(con, model: str = models.DEFAULT_MODEL) -> dict:
    """Quanti documenti e passaggi hanno già i vettori."""
    total = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    pending = pending_count(con, model)
    chunks = con.execute(f"SELECT COUNT(*) {_VALID}", (model,)).fetchone()[0]
    return {"docs": total, "pending": pending, "done": total - pending, "passages": chunks}


def open_store(dim: int | None = None, con=None):
    from .vectors import VectorStore, DatabaseVectors
    owned = con is None
    con = connect() if owned else con
    dim = dim or models.MODELS[models.DEFAULT_MODEL]["dim"]
    if getattr(con, "encrypted", False) or con.execute("SELECT 1 FROM sqlite_master WHERE name='vector_values'").fetchone():
        return DatabaseVectors(con, dim, owned)
    if owned:
        con.close()
    return VectorStore(DB_PATH, dim)


# ------------------------------------------------------------------ passaggi
def split_passages(text: str, words: int = PASSAGE_WORDS, overlap: int = OVERLAP_WORDS,
                   max_passages: int = MAX_PASSAGES) -> list[tuple[int, int]]:
    """Posizioni (inizio, fine) dei passaggi del testo: finestre di `words` parole che si sovrappongono di
    `overlap`, spezzate sempre tra una parola e l'altra."""
    step = max(1, words - overlap)
    spans = [m.span() for m in islice(_WORD.finditer(text), max_passages * step + overlap)]
    out = []
    for i in range(0, len(spans), step):
        j = min(i + words, len(spans))
        out.append((spans[i][0], spans[j - 1][1]))
        if len(out) >= max_passages or j >= len(spans):
            break
    return out


def _document_spans(con, doc_id, text, maximum):
    # Le finestre non attraversano il cambio di pagina. Paragrafi brevi contigui restano insieme.
    bounds = list(con.execute("SELECT min(start), max(end) FROM passaggi WHERE doc_id=? "
                              "GROUP BY pagina ORDER BY min(start)", (doc_id,)))
    spans = []
    for start, end in bounds or [(0, len(text))]:
        spans.extend((start + a, start + b) for a, b in
                     split_passages(text[start:end], max_passages=maximum - len(spans)))
        if len(spans) >= maximum:
            break
    return spans


# ------------------------------------------------------------------ calcolo (lavoratore)
def reconcile(con, store, model: str = models.DEFAULT_MODEL) -> int:
    """Toglie i passaggi di documenti cancellati, cambiati o interrotti a metà. Restituisce quanti."""
    store.clean_retired()
    stale = [r[0] for r in con.execute(
        "SELECT c.id FROM vec_chunks c LEFT JOIN vec_done v ON v.doc_id = c.doc_id "
        "LEFT JOIN docs d ON d.id = c.doc_id "
        "WHERE d.id IS NULL OR v.doc_id IS NULL OR v.mtime != d.mtime OR v.size != d.size OR v.model != ?",
        (model,))]
    if stale:
        store.remove(stale)
        con.executemany("DELETE FROM vec_chunks WHERE id = ?", [(i,) for i in stale])
    con.execute("DELETE FROM vec_done WHERE doc_id NOT IN (SELECT id FROM docs)")
    con.commit()
    total = len(store)
    if total and (total - store.count()) / total > COMPACT_RATIO:
        try:
            store.compact()
        except OSError:
            pass  # file in uso da una ricerca (Windows): si riprova la prossima volta
    return len(stale)


def embed_pending(cfg: dict, embedder, progress=None, stop=None, store=None) -> dict:
    """Calcola i vettori dei documenti che ne sono privi. Si può interrompere in ogni momento (`stop`) e
    riprende dal documento dopo l'ultimo completato. Rallenta in proporzione a `cpu_throttle`."""
    from .agent import acquire_lock
    lock = acquire_lock(str(DB_PATH) + ".embed.lock")
    if lock is None:
        return {"phase": "embed", "done": 0, "total": 0, "passages": 0, "removed": 0, "busy": True}
    with lock:
        return _embed_locked(cfg, embedder, progress, stop, store)


def _embed_locked(cfg, embedder, progress, stop, store):
    con = connect()
    store = open_store(embedder.dim, con) if store is None else store
    model = embedder.name
    st = {"phase": "embed", "done": 0, "total": 0, "passages": 0, "removed": 0}
    try:
        st["removed"] = reconcile(con, store, model)
        todo = [r[0] for r in con.execute(
            "SELECT d.id FROM docs d LEFT JOIN vec_done v ON v.doc_id = d.id "
            "WHERE v.doc_id IS NULL OR v.mtime != d.mtime OR v.size != d.size OR v.model != ? ORDER BY d.mtime DESC",
            (model,))]  # prima i documenti più recenti
        st["total"] = len(todo)
        idle_ratio = max(0.0, min(2.0, float(cfg.get("cpu_throttle", 0.5) or 0)))
        max_passages = max(1, int(cfg.get("semantic_max_passages", MAX_PASSAGES) or MAX_PASSAGES))
        pause_on_battery = bool(cfg.get("pause_on_battery"))
        for doc_id in todo:
            if stop is not None and stop.is_set():
                st["stopped"] = True
                break
            while pause_on_battery and background.on_battery() and not (stop is not None and stop.is_set()):
                st["paused"] = True
                if progress:
                    progress(dict(st))
                (stop.wait if stop is not None else time.sleep)(2.0)
            st["paused"] = False
            t0 = time.monotonic()
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT d.mtime, d.size, f.content FROM docs d JOIN fts f ON f.rowid = d.id "
                              "WHERE d.id = ?", (doc_id,)).fetchone()
            if row is None:
                con.rollback()
                continue
            mtime, size, content = row
            # Una rilettura può invalidare il documento dopo reconcile e prima del nostro turno.
            # I vecchi passaggi non devono tornare validi quando pubblichiamo i nuovi.
            previous = [r[0] for r in con.execute("SELECT id FROM vec_chunks WHERE doc_id=?", (doc_id,))]
            if previous:
                store.remove(previous)
                con.execute("DELETE FROM vec_chunks WHERE doc_id=?", (doc_id,))
            spans = _document_spans(con, doc_id, content or "", max_passages)
            ids = [con.execute("INSERT INTO vec_chunks(doc_id, start, end) VALUES(?,?,?)",
                               (doc_id, s, e)).lastrowid for s, e in spans]
            con.execute("INSERT OR REPLACE INTO vec_done(doc_id,mtime,size,model) VALUES(?,?,?,'')",
                        (doc_id, mtime, size))
            con.commit()  # prima le righe, poi i vettori, poi `vec_done`: un'interruzione si ripulisce con `reconcile`
            for i in range(0, len(spans), BATCH_STORE):
                if stop is not None and stop.is_set():
                    st["stopped"] = True
                    return st
                part = spans[i:i + BATCH_STORE]
                store.add(ids[i:i + BATCH_STORE], embedder.embed([content[s:e] for s, e in part]))
            # Se nel frattempo l'indicizzatore ha riletto il documento, ha cancellato il segnaposto.
            saved = con.execute("UPDATE vec_done SET model=? WHERE doc_id=? AND mtime=? AND size=? AND model=''",
                                (model, doc_id, mtime, size)).rowcount
            con.commit()
            if not saved:
                continue
            st["done"] += 1
            st["passages"] += len(spans)
            if progress:
                progress(dict(st))
            if idle_ratio:  # il lavoro nativo non si vede in thread_time: si usa il tempo reale
                time.sleep(min((time.monotonic() - t0) * idle_ratio, 2.0))
    finally:
        con.close()
    return st


# ------------------------------------------------------------------ ricerca
def _nearest(con, store, vector, model, min_score, k, margin, filters=("", ()), exclude=None):
    """Miglior passaggio per documento. Si applicano validità e filtri prima della classifica."""
    best: dict[int, dict] = {}
    condition, params = filters
    for keys, scores in store.scored_blocks(vector, min_score):
        for pos in range(0, len(keys), 400):  # anche SQLite con il vecchio limite di 999 parametri
            score = dict(zip(keys[pos:pos+400].tolist(), scores[pos:pos+400].tolist()))
            marks = ",".join("?" for _ in score)
            rows = con.execute(f"SELECT c.id,c.doc_id,c.start,c.end {_VALID} {condition} AND c.id IN ({marks})",
                               [model, *params, *score])
            for cid, doc_id, start, end in rows:
                if doc_id != exclude and (doc_id not in best or score[cid] > best[doc_id]["score"]):
                    best[doc_id] = {"doc_id": doc_id, "score": score[cid], "start": start, "end": end}
    ranked = sorted(best.values(), key=lambda h: (-h["score"], h["doc_id"]))[:k]
    return [h for h in ranked if h["score"] >= ranked[0]["score"] - margin]


def semantic_docs(con, store, embedder, text: str, min_score: float = MIN_SCORE,
                  k: int = SEMANTIC_HITS, margin: float = MARGIN, filters=("", ())) -> list[dict]:
    return _nearest(con, store, embedder.embed([text], "query")[0], embedder.name, min_score, k, margin, filters)


def similar_documents(con, doc_id, store=None, model=models.DEFAULT_MODEL, limit=12, min_score=0.90):
    """Dai vettori già calcolati, anche mentre un altro documento attende il modello."""
    keys = [r[0] for r in con.execute(f"SELECT c.id {_VALID} AND c.doc_id=?", (model, doc_id))]
    if not keys:
        return []
    store = open_store(con=con) if store is None else store
    vector = store.mean(keys)
    if vector is None:
        return []
    found = _nearest(con, store, vector, model, min_score, limit, MARGIN, exclude=doc_id)
    return _meaning_rows(con, found)


def _passage_text(con, doc_id: int, start: int, end: int) -> str:
    row = con.execute("SELECT substr(content, ?, ?) FROM fts WHERE rowid = ?", (start + 1, end - start, doc_id)).fetchone()
    text = re.sub(r"\s+", " ", row[0] if row and row[0] else "").strip()
    return text[:SNIPPET_CHARS] + ("…" if len(text) > SNIPPET_CHARS else "")


def _meaning_rows(con, found):
    rows = searcher.docs_by_id(con, [h["doc_id"] for h in found])
    return [dict(rows[h["doc_id"]], snippet=_passage_text(con, h["doc_id"], h["start"], h["end"]),
                 semantic=True, score=h["score"], offset=h["start"], end=h["end"])
            for h in found if h["doc_id"] in rows]


def fuse(lexical: list[dict], meaning: list[dict], limit: int) -> list[dict]:
    """Unisce le due classifiche (Reciprocal Rank Fusion). Le parole pesano il doppio: un documento che le
    contiene resta davanti a uno trovato solo per somiglianza di significato."""
    by_id = {r["id"]: r for r in lexical}
    for r in meaning:
        by_id.setdefault(r["id"], r)
    scores: dict[int, float] = {}
    for rank, r in enumerate(lexical):
        scores[r["id"]] = WEIGHT_WORDS / (RRF_K + rank + 1)
    for rank, r in enumerate(meaning):
        scores[r["id"]] = scores.get(r["id"], 0.0) + WEIGHT_MEANING / (RRF_K + rank + 1)
    lexical_ids = {r["id"] for r in lexical}
    order = sorted(scores, key=lambda i: (i not in lexical_ids, -scores[i]))
    return [by_id[i] for i in order[:limit]]


def hybrid_search(con, q: str, embedder, store=None, ext=None, order: str = "rank", limit: int = 500,
                  under: str | None = None, after: float | None = None, before: float | None = None,
                  where: str = searcher.WHERE_ALL, min_score: float = MIN_SCORE, margin: float = MARGIN) -> list[dict]:
    """Come `search.search`, ma con in più i documenti vicini per significato. Si usa solo per ricerche
    semplici (parole, filtri) ordinate per pertinenza; negli altri casi il risultato è quello per parole."""
    lexical = searcher.search(con, q, ext, order, limit, under, after, before, where)
    if order != "rank" or where == searcher.WHERE_NAME:  # i vettori sono dei contenuti: cercando solo nel nome no
        return lexical
    text, flt = searcher.plain_text_and_filters(q)
    if not text:
        return lexical
    if after is not None:
        flt["after"] = max(after, flt.get("after", 0.0))
    if before is not None:
        flt["before"] = min(before, flt.get("before", before))
    try:
        if store is None:
            store = open_store(embedder.dim, con)
        filters = searcher.document_filter(flt, flt.get("ext", ext), under)
        found = semantic_docs(con, store, embedder, text, min_score, margin=margin, filters=filters)
    except (OSError, ValueError, RuntimeError):
        return lexical  # vettori in riscrittura (compattazione), archivio danneggiato o errore del motore: solo parole
    if not found:
        return lexical
    return fuse(lexical, _meaning_rows(con, found), limit)
