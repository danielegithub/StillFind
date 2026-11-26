# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Ricerca per significato: passaggi, calcolo dei vettori (lavoratore) e ricerca ibrida (finestra, terminale).

Come funziona:
  * si rispettano i passaggi strutturali del documento. Solo quelli lunghi richiedono più vettori. La tabella
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

from stillfind.core.problems import ReadProblems
from stillfind.core.config import DB_PATH, DEFAULT_CONFIG
from stillfind.runtime.background import PowerMonitor
from stillfind.search.engine import SearchEngine
from stillfind.search.query import Query, WHERE_ALL, WHERE_NAME
from stillfind.semantic import models
from stillfind.semantic.models import ModelCatalog
from stillfind.storage.database import Database


class PassageSplitter:
    """Divide un testo in passaggi di circa 120 parole che si sovrappongono."""

    WORDS = 120
    OVERLAP = 20
    MAX_PASSAGES = 300       # per documento: oltre, il resto del documento non si legge «per significato»
    _WORD = re.compile(r"\S+")

    @classmethod
    def split(cls, text: str, words: int = WORDS, overlap: int = OVERLAP,
              max_passages: int = MAX_PASSAGES) -> list[tuple[int, int]]:
        """Posizioni (inizio, fine) dei passaggi del testo: finestre di `words` parole che si sovrappongono di
        `overlap`, spezzate sempre tra una parola e l'altra."""
        step = max(1, words - overlap)
        spans = [m.span() for m in islice(cls._WORD.finditer(text), max_passages * step + overlap)]
        out = []
        for i in range(0, len(spans), step):
            j = min(i + words, len(spans))
            out.append((spans[i][0], spans[j - 1][1]))
            if len(out) >= max_passages or j >= len(spans):
                break
        return out

    @classmethod
    def document_spans(cls, con, doc_id, text, maximum):
        """Passaggi di un documento: si rispettano quelli strutturali, solo i lunghi si spezzano."""
        bounds = list(con.execute("SELECT start,end FROM passaggi WHERE doc_id=? ORDER BY start LIMIT ?",
                                  (doc_id, maximum)))
        spans = []
        for start, end in bounds or [(0, len(text))]:
            spans.extend((start + a, start + b) for a, b in
                         cls.split(text[start:end], max_passages=maximum - len(spans)))
            if len(spans) >= maximum:
                break
        return spans


class SemanticIndex:
    """Stato dei vettori nell'archivio: quali documenti sono calcolati e quali no."""

    COMPACT_RATIO = 0.3      # si compatta l'archivio quando più del 30% delle righe è cancellato
    # righe dei passaggi con un calcolo valido: il documento esiste e non è cambiato dopo il calcolo
    VALID = ("FROM vec_chunks c JOIN vec_done v ON v.doc_id = c.doc_id "
             "JOIN docs d ON d.id = c.doc_id AND d.mtime = v.mtime AND d.size = v.size AND v.model = ?")

    def __init__(self, con, model: str = models.DEFAULT_MODEL):
        self.con, self.model = con, model

    @staticmethod
    def libraries_available() -> bool:
        """`onnxruntime` e `sentencepiece` sono installati? Si guarda senza importarli."""
        return all(importlib.util.find_spec(m) is not None for m in ("numpy", "onnxruntime", "sentencepiece"))

    @classmethod
    def availability(cls, cfg: dict, models_dir=None) -> str:
        """«on» se la ricerca per significato è attiva e utilizzabile; altrimenti il motivo:
        «off» (spenta nelle Impostazioni), «no_model» (file del modello assenti) o «no_libraries»."""
        if not cfg.get("semantic_search"):
            return "off"
        if not ModelCatalog.is_installed(models.DEFAULT_MODEL, models_dir):
            return "no_model"
        return "on" if cls.libraries_available() else "no_libraries"

    @classmethod
    def available(cls, cfg: dict, models_dir=None) -> bool:
        """La ricerca per significato è attiva nelle impostazioni e tutto il necessario è presente."""
        return cls.availability(cfg, models_dir) == "on"

    def pending_count(self) -> int:
        """Documenti con i vettori da calcolare o da ricalcolare."""
        return self.con.execute(
            "SELECT COUNT(*) FROM docs d LEFT JOIN vec_done v ON v.doc_id = d.id "
            "WHERE v.doc_id IS NULL OR v.mtime != d.mtime OR v.size != d.size OR v.model != ?",
            (self.model,)).fetchone()[0]

    def pending_ids(self) -> list[int]:
        """Identificativi dei documenti da calcolare, prima i più recenti."""
        return [r[0] for r in self.con.execute(
            "SELECT d.id FROM docs d LEFT JOIN vec_done v ON v.doc_id = d.id "
            "WHERE v.doc_id IS NULL OR v.mtime != d.mtime OR v.size != d.size OR v.model != ? ORDER BY d.mtime DESC",
            (self.model,))]

    def status(self) -> dict:
        """Quanti documenti e passaggi hanno già i vettori."""
        total = self.con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        pending = self.pending_count()
        chunks = self.con.execute(f"SELECT COUNT(*) {self.VALID}", (self.model,)).fetchone()[0]
        return {"docs": total, "pending": pending, "done": total - pending, "passages": chunks}

    # Un file privo di testo non ha passaggi da calcolare. «Lettura parziale» invece ha del testo.
    NO_TEXT = f"d.notext = 1 OR (d.error != '' AND d.error NOT LIKE '%{ReadProblems.PARTIAL_MARK}%')"

    def document_states(self, ids, cfg: dict, models_dir=None) -> dict[int, dict]:
        """Stato dei vettori di ciascun documento, senza caricare i vettori: solo `docs`, `vec_done` e il
        conteggio di `vec_chunks`. Per ogni identificativo restituisce un dizionario con `state`:
          * `no_text`  il file non ha testo leggibile (scansione senza OCR, errore di lettura): niente vettori;
          * `disabled` la ricerca per significato non è utilizzabile (`reason`: off, no_model, no_libraries);
          * `ready`    vettori calcolati sulla versione attuale del file (`passages`, `model`, `at_limit`);
          * `pending`  da calcolare: in coda, oppure `modified` se il file è cambiato dopo l'ultimo calcolo.
        `at_limit` segnala che i passaggi hanno raggiunto il tetto `semantic_max_passages`."""
        ids = list(ids)
        available = self.availability(cfg, models_dir)
        maximum = max(1, int(cfg.get("semantic_max_passages", PassageSplitter.MAX_PASSAGES)
                             or PassageSplitter.MAX_PASSAGES))
        out: dict[int, dict] = {}
        for i in range(0, len(ids), 400):
            batch = ids[i:i + 400]
            slots = ",".join("?" for _ in batch)
            sql = (f"SELECT d.id, {self.NO_TEXT}, v.doc_id IS NOT NULL, coalesce(v.model, ''), "
                   "coalesce(v.mtime = d.mtime AND v.size = d.size, 0), "
                   "(SELECT COUNT(*) FROM vec_chunks c WHERE c.doc_id = d.id) "
                   f"FROM docs d LEFT JOIN vec_done v ON v.doc_id = d.id WHERE d.id IN ({slots})")
            for doc, no_text, has_done, done_model, same_file, passages in self.con.execute(sql, batch):
                valid = bool(same_file) and done_model == self.model
                if no_text or (valid and not passages):
                    out[doc] = {"state": "no_text"}
                elif available != "on":
                    out[doc] = {"state": "disabled", "reason": available}
                elif valid:
                    out[doc] = {"state": "ready", "passages": passages, "model": done_model,
                                "at_limit": passages >= maximum, "max_passages": maximum}
                else:
                    # Quando il file cambia l'indicizzatore toglie `vec_done`, ma i vecchi passaggi restano in
                    # `vec_chunks` fino al nuovo calcolo: è così che si riconosce un file «modificato».
                    # model == '' è il segnaposto di un calcolo in corso: il documento è ancora in coda.
                    modified = passages > 0 and (not has_done or (done_model == self.model and not same_file))
                    out[doc] = {"state": "pending", "modified": modified}
        return out

    @staticmethod
    def open_store(dim: int | None = None, con=None):
        from stillfind.storage.vectors import VectorStore, DatabaseVectors
        owned = con is None
        con = Database.connect() if owned else con
        dim = dim or models.MODELS[models.DEFAULT_MODEL]["dim"]
        if getattr(con, "encrypted", False) or con.execute(
                "SELECT 1 FROM sqlite_master WHERE name='vector_values'").fetchone():
            return DatabaseVectors(con, dim, owned)
        if owned:
            con.close()
        return VectorStore(DB_PATH, dim)

    def reconcile(self, store) -> int:
        """Toglie i passaggi di documenti cancellati, cambiati o interrotti a metà. Restituisce quanti."""
        con, model = self.con, self.model
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
        if total and (total - store.count()) / total > self.COMPACT_RATIO:
            try:
                store.compact()
            except OSError:
                pass  # file in uso da una ricerca (Windows): si riprova la prossima volta
        return len(stale)


class EmbeddingJob:
    """Calcola i vettori dei documenti che ne sono privi (lavoro del processo lavoratore).

    Si può interrompere in ogni momento (`stop`) e riprende dal documento dopo l'ultimo completato.
    Rallenta in proporzione a `cpu_throttle`."""

    BATCH_STORE = 32         # passaggi calcolati e scritti per volta

    def __init__(self, cfg: dict, embedder, progress=None, stop=None, store=None):
        self.cfg, self.embedder, self.progress, self.stop, self.store = cfg, embedder, progress, stop, store

    def run(self) -> dict:
        from stillfind.core.locks import ProcessLock
        lock = ProcessLock.acquire(str(DB_PATH) + ".embed.lock")
        if lock is None:
            return {"phase": "embed", "done": 0, "total": 0, "passages": 0, "removed": 0, "busy": True}
        with lock:
            return self._run_locked()

    def _stopped(self) -> bool:
        return self.stop is not None and self.stop.is_set()

    def _run_locked(self) -> dict:
        cfg, embedder, progress, stop = self.cfg, self.embedder, self.progress, self.stop
        con = Database.connect()
        store = SemanticIndex.open_store(embedder.dim, con) if self.store is None else self.store
        index = SemanticIndex(con, embedder.name)
        st = {"phase": "embed", "done": 0, "total": 0, "passages": 0, "removed": 0}
        try:
            st["removed"] = index.reconcile(store)
            todo = index.pending_ids()  # prima i documenti più recenti
            st["total"] = len(todo)
            idle_ratio = max(0.0, min(2.0, float(cfg.get("cpu_throttle", 0.5) or 0)))
            max_passages = max(1, int(cfg.get("semantic_max_passages", PassageSplitter.MAX_PASSAGES)
                                      or PassageSplitter.MAX_PASSAGES))
            pause_on_battery = bool(cfg.get("pause_on_battery"))
            for doc_id in todo:
                if self._stopped():
                    st["stopped"] = True
                    break
                while pause_on_battery and PowerMonitor.on_battery() and not self._stopped():
                    st["paused"] = True
                    if progress:
                        progress(dict(st))
                    (stop.wait if stop is not None else time.sleep)(2.0)
                st["paused"] = False
                t0 = time.monotonic()
                outcome = self._embed_document(con, store, embedder, doc_id, max_passages, st)
                if outcome == "stopped":
                    return st
                if outcome != "saved":
                    continue
                if progress:
                    progress(dict(st))
                if idle_ratio:  # il lavoro nativo non si vede in thread_time: si usa il tempo reale
                    time.sleep(min((time.monotonic() - t0) * idle_ratio, 2.0))
        finally:
            con.close()
        return st

    def _embed_document(self, con, store, embedder, doc_id, max_passages, st) -> str:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT d.mtime, d.size, f.content FROM docs d JOIN fts f ON f.rowid = d.id "
                          "WHERE d.id = ?", (doc_id,)).fetchone()
        if row is None:
            con.rollback()
            return "missing"
        mtime, size, content = row
        # Una rilettura può invalidare il documento dopo reconcile e prima del nostro turno.
        # I vecchi passaggi non devono tornare validi quando pubblichiamo i nuovi.
        previous = [r[0] for r in con.execute("SELECT id FROM vec_chunks WHERE doc_id=?", (doc_id,))]
        if previous:
            store.remove(previous)
            con.execute("DELETE FROM vec_chunks WHERE doc_id=?", (doc_id,))
        spans = PassageSplitter.document_spans(con, doc_id, content or "", max_passages)
        ids = [con.execute("INSERT INTO vec_chunks(doc_id, start, end) VALUES(?,?,?)",
                           (doc_id, s, e)).lastrowid for s, e in spans]
        con.execute("INSERT OR REPLACE INTO vec_done(doc_id,mtime,size,model) VALUES(?,?,?,'')",
                    (doc_id, mtime, size))
        con.commit()  # prima le righe, poi i vettori, poi `vec_done`: un'interruzione si ripulisce con `reconcile`
        for i in range(0, len(spans), self.BATCH_STORE):
            if self._stopped():
                st["stopped"] = True
                return "stopped"
            part = spans[i:i + self.BATCH_STORE]
            store.add(ids[i:i + self.BATCH_STORE], embedder.embed([content[s:e] for s, e in part]))
        # Se nel frattempo l'indicizzatore ha riletto il documento, ha cancellato il segnaposto.
        saved = con.execute("UPDATE vec_done SET model=? WHERE doc_id=? AND mtime=? AND size=? AND model=''",
                            (embedder.name, doc_id, mtime, size)).rowcount
        con.commit()
        if not saved:
            return "stale"
        st["done"] += 1
        st["passages"] += len(spans)
        return "saved"


class SemanticSearch:
    """Ricerca per significato e ricerca ibrida (parole + significato) con la Reciprocal Rank Fusion."""

    MIN_SCORE = DEFAULT_CONFIG["semantic_min_score"]
    MARGIN = 0.015           # scarto dal migliore, tarato insieme alla soglia su 30 ricerche
    HITS = 60                # documenti distinti, ognuno con il proprio passaggio migliore
    RRF_K = 60               # costante della Reciprocal Rank Fusion
    WEIGHT_WORDS, WEIGHT_MEANING = 1.0, 0.5
    SNIPPET_CHARS = 240

    def __init__(self, con, embedder=None, store=None):
        self.con, self.embedder, self.store = con, embedder, store

    def nearest(self, store, vector, model, min_score, k, margin, filters=("", ()), exclude=None):
        """Miglior passaggio per documento. Si applicano validità e filtri prima della classifica."""
        con = self.con
        best: dict[int, dict] = {}
        condition, params = filters
        for keys, scores in store.scored_blocks(vector, min_score):
            for pos in range(0, len(keys), 400):  # anche SQLite con il vecchio limite di 999 parametri
                score = dict(zip(keys[pos:pos+400].tolist(), scores[pos:pos+400].tolist()))
                marks = ",".join("?" for _ in score)
                rows = con.execute(
                    f"SELECT c.id,c.doc_id,c.start,c.end {SemanticIndex.VALID} {condition} AND c.id IN ({marks})",
                    [model, *params, *score])
                for cid, doc_id, start, end in rows:
                    if doc_id != exclude and (doc_id not in best or score[cid] > best[doc_id]["score"]):
                        best[doc_id] = {"doc_id": doc_id, "score": score[cid], "start": start, "end": end}
        ranked = sorted(best.values(), key=lambda h: (-h["score"], h["doc_id"]))[:k]
        return [h for h in ranked if h["score"] >= ranked[0]["score"] - margin]

    def documents(self, store, text: str, min_score: float = MIN_SCORE, k: int = HITS,
                  margin: float = MARGIN, filters=("", ())) -> list[dict]:
        embedder = self.embedder
        return self.nearest(store, embedder.embed([text], "query")[0], embedder.name, min_score, k, margin, filters)

    def similar_documents(self, doc_id, store=None, model=models.DEFAULT_MODEL, limit=12, min_score=0.90):
        """Dai vettori già calcolati, anche mentre un altro documento attende il modello."""
        con = self.con
        keys = [r[0] for r in con.execute(f"SELECT c.id {SemanticIndex.VALID} AND c.doc_id=?", (model, doc_id))]
        if not keys:
            return []
        store = SemanticIndex.open_store(con=con) if store is None else store
        vector = store.mean(keys)
        if vector is None:
            return []
        found = self.nearest(store, vector, model, min_score, limit, self.MARGIN, exclude=doc_id)
        return self._meaning_rows(found)

    def _passage_text(self, doc_id: int, start: int, end: int) -> str:
        row = self.con.execute("SELECT substr(content, ?, ?) FROM fts WHERE rowid = ?",
                               (start + 1, end - start, doc_id)).fetchone()
        text = re.sub(r"\s+", " ", row[0] if row and row[0] else "").strip()
        return text[:self.SNIPPET_CHARS] + ("…" if len(text) > self.SNIPPET_CHARS else "")

    def _meaning_rows(self, found):
        rows = SearchEngine(self.con).documents_by_id([h["doc_id"] for h in found])
        return [dict(rows[h["doc_id"]], snippet=self._passage_text(h["doc_id"], h["start"], h["end"]),
                     semantic=True, score=h["score"], offset=h["start"], end=h["end"])
                for h in found if h["doc_id"] in rows]

    @classmethod
    def fuse(cls, lexical: list[dict], meaning: list[dict], limit: int) -> list[dict]:
        """Unisce le due classifiche (Reciprocal Rank Fusion). Le parole pesano il doppio: un documento che le
        contiene resta davanti a uno trovato solo per somiglianza di significato."""
        by_id = {r["id"]: r for r in lexical}
        for r in meaning:
            by_id.setdefault(r["id"], r)
        scores: dict[int, float] = {}
        for rank, r in enumerate(lexical):
            scores[r["id"]] = cls.WEIGHT_WORDS / (cls.RRF_K + rank + 1)
        for rank, r in enumerate(meaning):
            scores[r["id"]] = scores.get(r["id"], 0.0) + cls.WEIGHT_MEANING / (cls.RRF_K + rank + 1)
        lexical_ids = {r["id"] for r in lexical}
        order = sorted(scores, key=lambda i: (i not in lexical_ids, -scores[i]))
        return [by_id[i] for i in order[:limit]]

    def hybrid_search(self, q: str, ext=None, order: str = "rank", limit: int = 500,
                      under: str | None = None, after: float | None = None, before: float | None = None,
                      where: str = WHERE_ALL, min_score: float = MIN_SCORE, margin: float = MARGIN) -> list[dict]:
        """Come `SearchEngine.search`, ma con in più i documenti vicini per significato. Si usa solo per ricerche
        semplici (parole, filtri) ordinate per pertinenza; negli altri casi il risultato è quello per parole."""
        con, embedder = self.con, self.embedder
        lexical = SearchEngine(con).search(q, ext, order, limit, under, after, before, where)
        if order != "rank" or where == WHERE_NAME:  # i vettori sono dei contenuti: cercando solo nel nome no
            return lexical
        query = Query.parse(q)
        text, flt = query.plain_text(), query.filters
        if not text:
            return lexical
        if after is not None:
            flt["after"] = max(after, flt.get("after", 0.0))
        if before is not None:
            flt["before"] = min(before, flt.get("before", before))
        try:
            store = self.store
            if store is None:
                store = SemanticIndex.open_store(embedder.dim, con)
            filters = flt.document_clause(flt.get("ext", ext), under)
            found = self.documents(store, text, min_score, margin=margin, filters=filters)
        except (OSError, ValueError, RuntimeError):
            return lexical  # vettori in riscrittura (compattazione), archivio danneggiato o errore del motore: solo parole
        if not found:
            return lexical
        return self.fuse(lexical, self._meaning_rows(found), limit)
