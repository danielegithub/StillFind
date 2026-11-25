# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Ricerca per parole nell'indice FTS5."""
from __future__ import annotations

import re

from stillfind.core.stemmer import strip_accents
from stillfind.search.query import Query, SearchFilters, WHERE_ALL, WHERE_CONTENT, WHERE_NAME

HIT_START, HIT_END = "\x01", "\x02"  # marcatori usati da snippet()


def section_label(kind, title, number, state) -> str:
    if kind == "sheet":
        return f"Foglio {title}"
    if kind == "slide":
        return f"Slide {title}"
    return f"Pag. {number}" + (" · OCR" if state == "ocr" else "")


class SearchEngine:
    """Interroga l'archivio per parole, filtri e passaggi. Lavora su una connessione già aperta."""

    DATE_ORDER = "coalesce(a.data_iso,date(d.mtime,'unixepoch','localtime')) DESC,d.name COLLATE NOCASE"
    DOC_KEYS = ("id", "path", "name", "folder", "ext", "size", "mtime", "snippet", "ocr", "error",
                "document_date", "date_source")
    DOC_SELECT = ("SELECT d.id,d.path,d.name,d.folder,d.ext,d.size,d.mtime,'',d.ocr,d.error,a.data_iso,a.fonte "
                  "FROM docs d LEFT JOIN analisi a ON a.doc_id=d.id ")
    _ORDERS = {"rank": "bm25(fts, 8.0, 1.0, 0.3, 0.5)", "date": "d.mtime DESC",
               "document_date": DATE_ORDER, "name": "d.name COLLATE NOCASE"}

    def __init__(self, con):
        self.con = con

    # ---- ricerca
    def search(self, q: str | Query, ext=None, order: str = "rank", limit: int = 500,
               under: str | None = None, after: float | None = None, before: float | None = None,
               where: str = WHERE_ALL) -> list[dict]:
        """`ext`: una estensione o un elenco; `after` e `before`: solo documenti modificati dopo / prima di questi
        timestamp (`before` escluso); `where`: «all» (nome e contenuto), «content» o «name»."""
        con = self.con
        query = q if isinstance(q, Query) else Query.parse(q)
        flt = SearchFilters(query.filters)
        ext = flt.get("ext", ext)
        typed = bool(flt)
        if after is not None:
            flt["after"] = max(after, flt.get("after", 0.0))
        if before is not None:
            flt["before"] = min(before, flt.get("before", before))
        if not query.text:
            # solo filtri (es. «ext:pdf after:2025»): elenco dei documenti che li rispettano
            return self._show_fact_matches(self.list_documents(ext, order, limit, under, flt), flt) if typed else []
        match = query.fts(where)
        if not match:
            return []
        fsql, fargs = flt.document_clause(ext, under)
        order_sql = self._ORDERS[order]
        passage_query = query.passage_fts() if order == "rank" and where != WHERE_NAME else ""
        snippet = f"snippet(fts, 1, '{HIT_START}', '{HIT_END}', '…', 20)"
        prefix, join, start = "", "", "NULL"
        args = []
        if passage_query:
            # FTS lavora sui paragrafi indicizzati. A parità di passaggio, privilegia un estratto
            # che mostra più corrispondenze e conserva lo stesso brano per l'anteprima.
            prefix = self._best_passages_sql()
            join = " LEFT JOIN migliori b ON b.doc_id=d.id AND b.n=1 "
            order_sql = "b.doc_id IS NOT NULL DESC,b.evidenze DESC," + order_sql
            snippet = f"coalesce(b.brano,{snippet})"
            start = "b.start"
            args.append(passage_query)
        sql = prefix + f"""SELECT d.id, d.path, d.name, d.folder, d.ext, d.size, d.mtime,
                         {snippet}, d.ocr, d.error, a.data_iso, a.fonte,{start}
                  FROM fts JOIN docs d ON d.id = fts.rowid LEFT JOIN analisi a ON a.doc_id=d.id {join}
                  WHERE fts MATCH ?{fsql}
                  ORDER BY {order_sql} LIMIT ?"""
        match_arg = len(args)
        args.extend([match, *fargs])
        args += [limit]
        try:
            rows = con.execute(sql, args).fetchall()
        except con.OperationalError as ex:
            if "interrupt" in str(ex).lower():
                raise  # ricerca annullata dalla finestra perché ne è partita una più recente
            # sintassi FTS5 non valida: ripiega sulla ricerca semplice (mantenendo prefissi e stemming)
            safe = query.simple_fts(where)
            if not safe:
                return []
            args[match_arg] = safe
            rows = con.execute(sql, args).fetchall()
        results = [dict(zip((*self.DOC_KEYS, "passage_start"), r)) for r in rows]
        if not passage_query and where != WHERE_NAME and not query.is_raw:
            # L'ordine è già deciso. Per gli estratti interroghiamo soltanto i documenti visibili.
            content_query = query.simple_fts(WHERE_CONTENT)
            for i in range(0, len(results), 400):
                batch = {r["id"]: r for r in results[i:i + 400]}
                slots = ",".join("?" for _ in batch)
                passages = self._best_passages_sql(f" AND p.doc_id IN ({slots})")
                for doc, start, snippet in con.execute(passages + "SELECT doc_id,start,brano FROM migliori WHERE n=1",
                                                        (content_query, *batch)):
                    batch[doc].update(passage_start=start, snippet=snippet)
        return results

    @staticmethod
    def _best_passages_sql(scope: str = "") -> str:
        """Una riga per documento identifica il miglior brano che soddisfa tutta la query."""
        return f"""WITH trovati AS MATERIALIZED (
                SELECT p.doc_id,p.start,snippet(brani_fts,0,'{HIT_START}','{HIT_END}','…',20) AS brano,
                       brani_fts.rank AS peso,coalesce(pg.lettura='ocr',0) AS da_ocr
                FROM brani_fts JOIN passaggi p ON p.id=brani_fts.rowid
                LEFT JOIN pages pg ON pg.doc_id=p.doc_id AND pg.n=p.pagina
                WHERE brani_fts MATCH ?{scope}), estratti AS (
                SELECT *,length(brano)-length(replace(brano,'{HIT_START}','')) AS evidenze FROM trovati),
                migliori AS (SELECT *,row_number() OVER(PARTITION BY doc_id ORDER BY evidenze DESC,peso,da_ocr,start) AS n
                             FROM estratti) """

    def _show_fact_matches(self, rows: list[dict], flt: SearchFilters) -> list[dict]:
        first = next(flt.fact_clauses(), None)
        if first:
            clause, args, alias = first
            for row in rows:
                match = self.con.execute(f"SELECT {alias}.start,{alias}.end {clause} AND p.doc_id=? "
                                         f"ORDER BY {alias}.start LIMIT 1", (*args, row["id"])).fetchone()
                if match:
                    a, b = match
                    left = max(0, a - 65)
                    before, value, after = self.con.execute(
                        "SELECT substr(content,?,?),substr(content,?,?),substr(content,?,80) FROM fts WHERE rowid=?",
                        (left + 1, a - left, a + 1, b - a, b + 1, row["id"])).fetchone()
                    row.update(offset=a, end=b, snippet=before + HIT_START + value + HIT_END + after)
        return rows

    # ---- elenchi di documenti
    def documents_by_id(self, ids, ext=None, under: str | None = None, flt: dict | None = None) -> dict[int, dict]:
        """Righe dei documenti indicati (nello stesso formato dei risultati), con gli stessi filtri della ricerca."""
        ids = list(ids)
        if not ids:
            return {}
        fsql, fargs = SearchFilters(flt or {}).document_clause(ext, under)
        sql = self.DOC_SELECT + f"WHERE d.id IN ({','.join('?' * len(ids))})" + fsql
        return {r[0]: dict(zip(self.DOC_KEYS, r)) for r in self.con.execute(sql, ids + fargs)}

    def list_documents(self, ext=None, order: str = "name", limit: int = 500,
                       under: str | None = None, flt: dict | None = None) -> list[dict]:
        """Documenti indicizzati (di una cartella e sottocartelle) senza ricerca testuale."""
        fsql, fargs = SearchFilters(flt or {}).document_clause(ext, under)
        order_sql = {"date": "d.mtime DESC", "document_date": self.DATE_ORDER}.get(order, "d.name COLLATE NOCASE")
        sql = self.DOC_SELECT + "WHERE 1=1" + fsql + f" ORDER BY {order_sql} LIMIT ?"
        return [dict(zip(self.DOC_KEYS, r)) for r in self.con.execute(sql, [*fargs, limit])]

    # ---- posizioni nel documento
    def page_at(self, doc_id: int, offset: int) -> int | None:
        """Numero della pagina del documento che contiene la posizione `offset` del testo; None se il documento
        non ha pagine (solo i PDF le hanno)."""
        row = self.con.execute("SELECT n FROM pages WHERE tipo='page' AND doc_id = ? AND start <= ? "
                               "ORDER BY n DESC LIMIT 1", (doc_id, max(0, offset))).fetchone()
        return row[0] if row else None

    def add_pages(self, rows: list[dict], limit: int | None = None) -> None:
        """Riferimento del primo termine evidenziato o del brano già individuato."""
        con = self.con
        for r in rows[:limit]:
            r["page"] = r["location"] = None
            doc_id = r["id"]
            if not con.execute("SELECT 1 FROM pages WHERE doc_id=? LIMIT 1", (doc_id,)).fetchone():
                continue
            offset = r.get("offset")
            if offset is None:
                offset = r.get("passage_start")
            if offset is None:
                snip = r.get("snippet") or ""
                if HIT_START not in snip:
                    continue
                snip = snip.removeprefix("…").removesuffix("…")
                before = snip.split(HIT_START, 1)[0]
                plain = snip.replace(HIT_START, "").replace(HIT_END, "")
                row = con.execute("SELECT instr(content,?) FROM fts WHERE rowid=?", (plain, doc_id)).fetchone()
                if not row or not row[0]:
                    continue
                offset = row[0] - 1 + len(before)
            row = con.execute("SELECT tipo,titolo,n,lettura FROM pages WHERE doc_id=? AND start<=? "
                              "ORDER BY n DESC LIMIT 1", (doc_id, offset)).fetchone()
            if row:
                r["location"] = section_label(*row)
                r["page"] = row[2] if row[0] == "page" else None

    # ---- suggerimenti
    @staticmethod
    def _edit_distance(a: str, b: str, limit: int) -> int:
        """Distanza di Damerau-Levenshtein (con scambio di lettere vicine); `limit + 1` se supera il limite."""
        if abs(len(a) - len(b)) > limit:
            return limit + 1
        prev2, prev = None, list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cost = 0 if ca == cb else 1
                v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
                if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                    v = min(v, prev2[j - 2] + 1)
                cur.append(v)
            if min(cur) > limit:
                return limit + 1
            prev2, prev = prev, cur
        return prev[-1]

    def suggest(self, q: str | Query) -> str | None:
        """«Intendevi…?»: se una parola cercata non esiste nell'archivio, propone la parola più vicina tra quelle
        realmente presenti (tabella virtuale `fts5vocab`, nessun indice in più). None se non serve."""
        con = self.con
        text = (q if isinstance(q, Query) else Query.parse(q)).text
        text = re.sub(r"\b(?:name|content|stem|nstem):", " ", text)
        words = [w for w in re.findall(r"[^\W\d_]+", text) if w.upper() not in Query.OPERATORS and len(w) >= 4]
        if not words:
            return None
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_vocab USING fts5vocab(main, fts, 'col')")
        result, changed = text, False
        for w in words:
            key = strip_accents(w.lower())
            if con.execute("SELECT 1 FROM temp.fts_vocab WHERE col IN ('name', 'content') AND term >= ? AND term < ? "
                           "LIMIT 1",
                           (key, key + "\U0010ffff")).fetchone():
                continue  # esiste almeno una parola che comincia così (anche come prefisso)
            limit = 1 if len(key) <= 5 else 2
            first = key[0]
            best: tuple[int, int, str] | None = None
            for term, doc in con.execute(
                    "SELECT term, SUM(doc) FROM temp.fts_vocab WHERE col IN ('name', 'content') AND term >= ? AND term < ? "
                    "AND length(term) BETWEEN ? AND ? GROUP BY term",
                    (first, first + "\U0010ffff", len(key) - limit, len(key) + limit)):
                d = self._edit_distance(key, term, limit)
                if d <= limit and (best is None or (d, -doc) < (best[0], -best[1])):
                    best = (d, doc, term)
            if best:
                result = re.sub(rf"\b{re.escape(w)}\b", best[2], result, count=1)
                changed = True
        return result.strip() if changed else None
