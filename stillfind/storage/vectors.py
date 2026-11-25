# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Archivio dei vettori per la ricerca per significato, letto da file mappato in memoria (`numpy.memmap`).

Ogni passaggio ha un vettore di `dim` numeri, salvato come int8 (1 byte per numero) in un file accanto al
database; un secondo file tiene l'identificativo del passaggio. Cercare non carica l'archivio: il sistema
porta in memoria solo le pagine lette e la ricerca procede a blocchi, quindi la RAM resta quasi invariata
anche con centinaia di migliaia di vettori. Con 200.000 passaggi da 384 numeri sono circa 77 MB su disco."""
from __future__ import annotations

import os

import numpy as np

from stillfind.storage.vector_files import CHUNK_ROWS, DELETED, VectorFiles


class VectorStore(VectorFiles):
    def _maps(self):
        while True:
            generation = self._generation()
            vec, ids = self._paths(generation)
            try:
                with ids.open("rb") as fi, vec.open("rb") as fv:
                    n = os.fstat(fi.fileno()).st_size // 8
                    if n == 0:
                        return None, None
                    return (np.memmap(fv, dtype=np.int8, mode="r", shape=(n, self.dim)),
                            np.memmap(fi, dtype=np.int64, mode="r", shape=(n,)))
            except FileNotFoundError:
                if self._generation() == generation:
                    if generation:
                        raise
                    return None, None

    def count(self) -> int:
        """Vettori validi (non cancellati)."""
        _, ids = self._maps()
        return 0 if ids is None else int((np.asarray(ids) != DELETED).sum())

    # ---- scrittura
    @staticmethod
    def _unit(v: np.ndarray) -> np.ndarray:
        v = np.atleast_2d(np.asarray(v, dtype=np.float32))
        norm = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.where(norm == 0, 1, norm)

    def add(self, ids, vectors) -> None:
        """Aggiunge vettori (anche float32 qualunque: si normalizzano e si riducono a int8)."""
        ids = np.asarray(list(ids), dtype=np.int64)
        q = np.rint(self._unit(vectors) * 127).astype(np.int8)
        if q.shape != (len(ids), self.dim):
            raise ValueError(f"attesi {len(ids)} vettori da {self.dim} numeri, ricevuti {q.shape}")
        self.vec_path.parent.mkdir(parents=True, exist_ok=True)
        self._trim_unfinished()
        with open(self.vec_path, "ab") as f:
            f.write(q.tobytes())
        with open(self.ids_path, "ab") as f:  # per ultimo: la riga esiste solo se il vettore è già scritto
            f.write(ids.tobytes())

    def _trim_unfinished(self) -> None:
        """Se un'interruzione ha lasciato vettori scritti ma non registrati (il file `.ids` è l'ultimo a essere
        scritto), li toglie: altrimenti i nuovi vettori finirebbero sfalsati rispetto agli identificativi."""
        vec, ids = self._paths(self._generation())
        nv = vec.stat().st_size if vec.exists() else 0
        ni = ids.stat().st_size if ids.exists() else 0
        rows = min(nv // self.dim, ni // 8)
        for path, size, want in ((vec, nv, rows*self.dim), (ids, ni, rows*8)):
            if size != want:
                with path.open("r+b") as f:
                    f.truncate(want)

    def remove(self, ids) -> int:
        """Cancella i vettori dei passaggi indicati (si compatta poi con `compact`). Restituisce quanti."""
        n = len(self)
        if n == 0:
            return 0
        mm = np.memmap(self.ids_path, dtype=np.int64, mode="r+", shape=(n,))
        hit = np.isin(mm, np.asarray(list(ids), dtype=np.int64))
        mm[hit] = DELETED
        mm.flush()
        del mm
        return int(hit.sum())

    # ---- ricerca
    def mean(self, wanted):
        """Centro dei passaggi di un documento, senza caricare il modello o l'intero archivio."""
        vec, ids = self._maps()
        if vec is None or not wanted:
            return None
        wanted = np.asarray(wanted, dtype=np.int64)
        total = np.zeros(self.dim, dtype=np.float32)
        for start in range(0, len(ids), CHUNK_ROWS):
            keep = np.isin(ids[start:start + CHUNK_ROWS], wanted)
            if keep.any():
                total += vec[start:start + CHUNK_ROWS][keep].sum(axis=0, dtype=np.float32)
        return self._unit(total)[0] if total.any() else None

    def scored_blocks(self, query, minimum):
        vec, ids = self._maps()
        if vec is None:
            return
        q = self._unit(query)[0] / 127.0
        for start in range(0, len(ids), CHUNK_ROWS):
            keys = ids[start:start + CHUNK_ROWS]
            scores = np.asarray(vec[start:start + CHUNK_ROWS], dtype=np.float32) @ q
            keep = (keys != DELETED) & (scores >= minimum)
            yield keys[keep], scores[keep]

    def search(self, query, k: int = 10) -> list[tuple[int, float]]:
        """I `k` passaggi più simili: [(id, somiglianza da -1 a 1)], dal più simile. Procede a blocchi."""
        vec, ids = self._maps()
        if vec is None:
            return []
        q = self._unit(query)[0] / 127.0
        best_ids = np.empty(0, dtype=np.int64)
        best_scores = np.empty(0, dtype=np.float32)
        for start in range(0, len(ids), CHUNK_ROWS):
            block_ids = np.asarray(ids[start:start + CHUNK_ROWS])
            scores = np.asarray(vec[start:start + CHUNK_ROWS]).astype(np.float32) @ q
            scores[block_ids == DELETED] = -np.inf
            top = np.argpartition(scores, -min(k, len(scores)))[-k:]
            best_ids = np.concatenate([best_ids, block_ids[top]])
            best_scores = np.concatenate([best_scores, scores[top]])
            if len(best_ids) > k:  # si tengono solo i migliori trovati finora
                keep = np.argpartition(best_scores, -k)[-k:]
                best_ids, best_scores = best_ids[keep], best_scores[keep]
        order = np.argsort(-best_scores)
        return [(int(best_ids[i]), float(best_scores[i])) for i in order if best_scores[i] != -np.inf][:k]


class DatabaseVectors:
    """I blocchi sono pagine SQLCipher. Memoria limitata a un blocco anche nella ricerca."""
    def __init__(self, con, dim, owned=False):
        self.con, self.dim, self.owned = con, dim, owned

    def close(self):
        if self.owned:
            self.con.close()
            self.owned = False

    def __len__(self):
        return self.con.execute("SELECT count(*) FROM vector_values").fetchone()[0]

    count = __len__

    def clean_retired(self):
        pass

    def compact(self):
        return 0

    def add(self, ids, vectors):
        block = np.rint(VectorStore._unit(vectors) * 127).astype(np.int8)
        if block.shape != (len(ids), self.dim):
            raise ValueError("Dimensione dei vettori non valida.")
        self.con.executemany("INSERT OR REPLACE INTO vector_values VALUES(?,?)",
                             ((int(i), v.tobytes()) for i, v in zip(ids, block)))
        self.con.commit()

    def remove(self, ids):
        return self.con.executemany("DELETE FROM vector_values WHERE id=?", ((int(i),) for i in ids)).rowcount

    def _blocks(self, ids=None):
        if ids is None:
            queries = [("SELECT id,value FROM vector_values", ())]
        else:
            queries = [("SELECT id,value FROM vector_values WHERE id IN (" +
                        ",".join("?" for _ in batch) + ")", batch)
                       for i in range(0, len(ids), 400) if (batch := ids[i:i+400])]
        for sql, params in queries:
            cur = self.con.execute(sql, params)
            while rows := cur.fetchmany(CHUNK_ROWS):
                yield (np.array([r[0] for r in rows], dtype=np.int64),
                       np.frombuffer(b"".join(r[1] for r in rows), dtype=np.int8).reshape(-1, self.dim))

    def scored_blocks(self, query, minimum):
        q = VectorStore._unit(query)[0] / 127.0
        for ids, vec in self._blocks():
            scores = vec.astype(np.float32) @ q
            keep = scores >= minimum
            yield ids[keep], scores[keep]

    def mean(self, wanted):
        total = np.zeros(self.dim, dtype=np.float32)
        for _, values in self._blocks(list(wanted)):
            total += values.sum(axis=0, dtype=np.float32)
        return VectorStore._unit(total)[0] if total.any() else None

    def search(self, query, k=10):
        import heapq
        return heapq.nlargest(k, ((int(i), float(s)) for ids, scores in self.scored_blocks(query, -2)
                                   for i, s in zip(ids, scores)), key=lambda pair: pair[1])
