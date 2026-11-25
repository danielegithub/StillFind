# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Anteprima di un risultato: brani attorno alle occorrenze, insensibili agli accenti."""
from __future__ import annotations

import sqlite3
from stillfind.storage.protection import ArchiveProtection, Locked
import unicodedata
from bisect import bisect_right

from stillfind.core.config import DB_PATH
from stillfind.search.engine import HIT_END, HIT_START, section_label
from stillfind.core.stemmer import stem, strip_accents
from stillfind.core.labels import thousands

import re


FULL_TEXT_LIMIT = 400_000   # caratteri mostrati nel «Testo completo» (la copia comprende sempre tutto il testo)
MAX_HITS = 5000             # occorrenze evidenziate al massimo nel testo completo
OCCURRENCES_SHOWN = 50      # brani mostrati in «Occorrenze»


class Layout:
    """Intervalli in caratteri del testo indicizzato, prima di ogni formattazione."""
    def __init__(self, passages=(), sections=()):
        self.passages = list(passages)
        self.starts = [a for a, _ in self.passages]
        self.sections = list(sections)
        self.section_starts = [r[0] for r in self.sections]

    def at(self, offset, length):
        left, right, label, page = 0, length, "", None
        i = bisect_right(self.section_starts, offset)
        if i < len(self.sections):
            right = self.section_starts[i]
        if i:
            left, kind, title, n, state = self.sections[i-1]
            label = section_label(kind, title, n, state)
            page = n if kind == "page" else None
        j = bisect_right(self.starts, offset) - 1
        if j >= 0:
            a, b = self.passages[j]
            if offset < b:
                left, right = max(left, a), min(right, b)
        return left, right, label, page



class DocumentPreview:
    """Anteprima dei documenti: occorrenze, estratti e testo completo con le parole evidenziate."""

    @staticmethod
    def _norm_with_map(text: str) -> tuple[str, list[int] | None]:
        """Rimuove gli accenti restituendo (testo_normalizzato, mappa_indice_originale)."""
        if text.isascii():
            return text, None
        chars: list[str] = []
        pos: list[int] = []
        for i, ch in enumerate(text):
            if ord(ch) < 128:
                chars.append(ch)
                pos.append(i)
            else:
                for c in unicodedata.normalize("NFD", ch):
                    if unicodedata.category(c) != "Mn":
                        chars.append(c)
                        pos.append(i)
        return "".join(chars), pos

    @staticmethod
    def _map_span(orig: str, pos: list[int] | None, a: int, b: int) -> tuple[int, int]:
        if pos is None:
            return a, b
        if a >= len(pos) or b <= a:
            return len(orig), len(orig)
        oa = pos[a]
        ob = pos[b - 1] + 1
        while ob < len(orig) and unicodedata.category(orig[ob]) == "Mn":
            ob += 1
        return oa, ob

    @staticmethod
    def _term_regex(terms: list[str]):
        """Espressione che riconosce le parole cercate, le loro forme (radice) e senza accenti; None se non ce ne sono."""
        pats: list[str] = []
        for t in terms:
            st = stem(t)
            if len(st) >= 3:
                pats.append(r"\b" + re.escape(st) + r"\w*")
            nt = strip_accents(t)
            if nt:
                pats.append(re.escape(nt))
        return re.compile("|".join(dict.fromkeys(pats)), re.I) if pats else None

    @staticmethod
    def hit_spans(content: str, terms: list[str], limit: int = MAX_HITS) -> list[tuple[int, int]]:
        """Posizioni (inizio, fine) di tutte le occorrenze nel testo, nell'ordine; insensibile agli accenti.
        Il testo si legge a blocchi (non si duplica per intero) e si ferma a `limit` occorrenze."""
        rx = DocumentPreview._term_regex(terms)
        if rx is None or not content:
            return []
        out: list[tuple[int, int]] = []
        chunk_size, overlap, offset, n, last_end = 100_000, 64, 0, len(content), 0
        while offset < n and len(out) < limit:
            chunk = content[offset:offset + chunk_size]
            norm, pos = DocumentPreview._norm_with_map(chunk)
            for m in rx.finditer(norm):
                oa, ob = DocumentPreview._map_span(chunk, pos, m.start(), m.end())
                if offset + oa < last_end or ob <= oa:
                    continue  # già vista nel blocco precedente (zona di sovrapposizione)
                out.append((offset + oa, offset + ob))
                last_end = offset + ob
                if len(out) >= limit:
                    break
            if offset + chunk_size >= n:
                break
            offset += chunk_size - overlap
        return out

    @staticmethod
    def page_for(starts: list[int], offset: int) -> int | None:
        """Pagina (da 1) che contiene `offset`, date le posizioni di inizio delle pagine; None se non ce ne sono."""
        return bisect_right(starts, offset) or None if starts else None

    @staticmethod
    def occurrences(content: str, terms: list[str], starts: list[int] | None = None, max_items: int = 12,
                    width: int = 70, gap: int = 200, layout=None) -> tuple[list[dict], int]:
        """Le occorrenze delle parole cercate come voci per l'elenco «Altre occorrenze».
        Restituisce (voci, numero di occorrenze nel documento). Ogni voce: `index` (posizione tra tutte le occorrenze,
        la stessa del testo completo nell'anteprima), `page` (o None) e `parts`: [(testo, "hit" | "")] con un po'
        di contesto, e `count` (quante occorrenze contiene). Occorrenze a meno di `gap` caratteri dalla voce precedente
        e nella stessa pagina stanno nella stessa voce."""
        spans = DocumentPreview.hit_spans(content, terms)
        items: list[dict] = []
        layout = layout or Layout(sections=[(s, "page", "", n, "") for n, s in enumerate(starts or [], 1)])
        last_end, last_start = -gap, None
        for k, (a, b) in enumerate(spans):
            left, right, label, page = layout.at(a, len(content))
            if items and a - last_end < gap and left == last_start:
                items[-1]["count"] += 1
                last_end = b
                continue
            if len(items) >= max_items:
                break
            last_end, last_start = b, left
            lo, hi = max(left, a - width), min(right, b + width)
            text = content[lo:hi].replace("\r", " ").replace("\n", " ")  # stessa lunghezza: le posizioni restano valide
            parts, pos = [], lo
            for x, y in spans[k:]:
                if x >= hi:
                    break
                parts += [(text[pos - lo:x - lo], ""), (text[x - lo:min(y, hi) - lo], "hit")]
                pos = min(y, hi)
            parts.append((text[pos - lo:], ""))
            parts[0] = (("…" if lo > 0 else "") + parts[0][0], "")
            parts[-1] = (parts[-1][0] + ("…" if hi < len(content) else ""), parts[-1][1])
            items.append({"index": k, "page": page, "location": label, "count": 1, "parts": [p for p in parts if p[0]]})
        return items, len(spans)

    @staticmethod
    def _full_range(content, lo, hi, hits, layout):
        # Ordina intestazioni ed evidenziazioni nelle coordinate del testo originale.
        # I ritorni a capo di Windows vengono normalizzati soltanto per la visualizzazione.
        events = []
        for a, b in hits:
            events.extend(((a, 2, "hit"), (b, 0, "")))
        if layout:
            states = {"native": "testo digitale", "short": "testo breve, OCR da eseguire",
                      "error": "errore OCR", "pending": "OCR da eseguire", "ocr_empty": "OCR senza testo",
                      "native_ocr_empty": "testo digitale, OCR senza testo"}
            first = max(0, bisect_right(layout.section_starts, lo) - 1)
            for start, kind, title, number, state in layout.sections[first:]:
                if start > hi or start == hi < len(content):
                    break
                heading = section_label(kind, title, number, state)
                if state in states:
                    heading += " · " + states[state]
                events.append((max(lo, start), 1, heading))
        pos, tag, parts = lo, "", []
        for offset, event, value in sorted(events):
            if offset > pos:
                parts.append((content[pos:offset], tag))
            if event == 1:
                parts.append((("\n" if offset > lo else "") + value + "\n", "title"))
            else:
                tag = value
            pos = offset
        parts.append((content[pos:hi], tag))
        return [(t.replace("\r\n", "\n").replace("\r", "\n"), k) for t, k in parts if t]

    @staticmethod
    def full_text_parts(content: str, terms: list[str], limit: int = FULL_TEXT_LIMIT, layout=None) -> list[tuple[str, str]]:
        if not content.strip() and not (layout and layout.sections):
            return [("Nessun testo estratto: il file è stato trovato solo per il nome.", "dim")]
        hi = min(len(content), limit)
        parts = DocumentPreview._full_range(content, 0, hi, DocumentPreview.hit_spans(content[:hi], terms), layout)
        if hi < len(content):
            parts.append((f"\n\n… Il testo continua: qui si vedono i primi {thousands(limit)} caratteri su "
                          f"{thousands(len(content))}. «Copia testo» copia tutto il documento.", "dim"))
        return parts

    @staticmethod
    def anchored_parts(content, start, end, full=False, limit=FULL_TEXT_LIMIT, layout=None):
        """Anteprima posizionata sui caratteri del testo originale, anche oltre i primi 400.000."""
        a = max(0, min(start, len(content)))
        b = min(len(content), max(a + 1, end))
        left, right, label, _ = (layout or Layout()).at(a, len(content))
        if full:
            # Centriamo la finestra sul punto richiesto solo quando altrimenti resterebbe fuori.
            lo = min(max(0, len(content) - limit), max(0, a - limit // 2)) if b > limit else 0
            hi = min(len(content), max(lo + limit, b))
        else:
            b = min(b, right)
            lo, hi = max(left, a - 150), min(right, b + 150)
        parts = [(label + "\n", "title")] if label and not full else []
        if lo > (0 if full else left):
            parts.append(("…\n", "dim"))
        parts.extend(DocumentPreview._full_range(content, lo, hi, [(a, b)], layout if full else None))
        if hi < (len(content) if full else right):
            parts.append(("\n…", "dim"))
        return [(t.replace("\r\n", "\n").replace("\r", "\n"), tag) for t, tag in parts if t]

    @staticmethod
    def _excerpts(content, terms, width, max_n, layout, preferred=None):
        skip = None
        if preferred is not None:
            lo, hi, label, _ = layout.at(preferred, len(content))
            skip = lo, hi
            for text, marks, _, cut_left, cut_right in DocumentPreview._excerpts(content[lo:hi], terms, width, max_n, Layout()):
                yield text, marks, label, cut_left, cut_right
                max_n -= 1
            if max_n <= 0 or hi - lo == len(content):
                return
        hits = DocumentPreview.hit_spans(content, terms)
        if skip:
            hits = [(a, b) for a, b in hits if not skip[0] <= a < skip[1]]
        i = 0
        for _ in range(max_n):
            if i >= len(hits):
                break
            a, b = hits[i]
            left, right, label, _ = layout.at(a, len(content))
            lo, hi = max(left, a-width), min(right, b+width)
            marks = []
            while i < len(hits) and hits[i][0] < hi:
                x, y = hits[i]
                marks.append((x-lo, min(y, hi)-lo))
                i += 1
            yield content[lo:hi], marks, label, lo > left, hi < right

    @staticmethod
    def excerpts(content: str, terms: list[str], width: int = 140, max_n: int = 8):
        if not terms:
            return [(content[:600], [])] if content else []
        return [(piece, marks) for piece, marks, *_ in DocumentPreview._excerpts(content, terms, width, max_n, Layout())]

    @staticmethod
    def snippet_parts(snippet: str) -> list[tuple[str, str]]:
        """Trasforma lo snippet già calcolato da FTS5 in parti (testo, tag) per l'anteprima.
        Vuoto se lo snippet non contiene occorrenze (ad esempio match solo sul nome del file)."""
        if not snippet or HIT_START not in snippet:
            return []
        parts: list[tuple[str, str]] = []
        # i puntini ci sono già nello snippet di FTS5, e solo dove il testo è tagliato: non se ne aggiungono altri
        for i, chunk in enumerate(snippet.replace("\r\n", "\n").split(HIT_START)):
            if i == 0:
                parts.append((chunk, ""))
                continue
            hit, _, rest = chunk.partition(HIT_END)
            parts += [(hit, "hit"), (rest, "")]
        parts.append(("\n", ""))
        return [p for p in parts if p[0]]

    @staticmethod
    def preview_parts(content: str, terms: list[str], max_n: int = 8, layout=None, preferred=None) -> list[tuple[str, str]]:
        if not content.strip():
            return [("Nessun testo estratto: il file è stato trovato solo per il nome.", "dim")]
        layout = layout or Layout()
        parts = []
        for text, marks, label, cut_left, cut_right in DocumentPreview._excerpts(content, terms, 140, max_n, layout, preferred):
            if label:
                parts.append((label + "\n", "title"))
            if cut_left:
                parts.append(("…", "dim"))
            pos = 0
            for a, b in marks:
                parts += [(text[pos:a], ""), (text[a:b], "hit")]
                pos = b
            parts += [(text[pos:], ""), ("…\n\n" if cut_right else "\n\n", "dim")]
        if not parts:
            if terms:
                parts.append(("Nessuna occorrenza nel contenuto (trovato nel nome del file). Anteprima iniziale:\n\n", "dim"))
            # Anche l'anteprima iniziale conserva le intestazioni quando attraversa più sezioni.
            end = min(800, len(content))
            pos = 0
            for start, kind, title, n, state in layout.sections:
                if start >= end:
                    break
                parts += [(content[pos:start], ""), (section_label(kind, title, n, state) + "\n", "title")]
                pos = start
            parts.append((content[pos:end], ""))
        return [(t.replace("\r\n", "\n").replace("\r", "\n"), tag) for t, tag in parts if t]

    @staticmethod
    def read_document(doc_id):
        con = ArchiveProtection.connection(DB_PATH)
        try:
            con.execute("BEGIN")
            row = con.execute("SELECT content FROM fts WHERE rowid=?", (doc_id,)).fetchone()
            passages = con.execute("SELECT start,end FROM passaggi WHERE doc_id=? ORDER BY start", (doc_id,))
            sections = con.execute("SELECT start,tipo,titolo,n,lettura FROM pages WHERE doc_id=? ORDER BY n", (doc_id,))
            return (row[0] if row else "") or "", Layout(passages, sections)
        finally:
            con.close()

    @staticmethod
    def read_page_starts(doc_id: int) -> list[int]:
        """Posizioni in cui cominciano le pagine del documento (vuoto se non ha pagine), con una connessione propria."""
        con = ArchiveProtection.connection(DB_PATH)
        try:
            return [r[0] for r in con.execute("SELECT start FROM pages WHERE doc_id=? AND tipo='page' ORDER BY n", (doc_id,))]
        finally:
            con.close()

    @staticmethod
    def read_content(doc_id: int) -> str:
        """Testo indicizzato di un documento, letto con una connessione propria e di breve durata
        (la connessione della finestra non si può usare da un altro thread)."""
        con = ArchiveProtection.connection(DB_PATH)
        try:
            row = con.execute("SELECT content FROM fts WHERE rowid=?", (doc_id,)).fetchone()
        finally:
            con.close()
        return row[0] if row and row[0] else ""
