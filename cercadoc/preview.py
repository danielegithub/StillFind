# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Anteprima di un risultato: brani attorno alle occorrenze, insensibili agli accenti."""
from __future__ import annotations

import sqlite3
from . import protection
import unicodedata
from bisect import bisect_right

from .config import DB_PATH
from .search import HIT_END, HIT_START
from .stemmer import stem, strip_accents
from .ui_text import thousands

import re


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


FULL_TEXT_LIMIT = 400_000   # caratteri mostrati nel «Testo completo» (la copia comprende sempre tutto il testo)
MAX_HITS = 5000             # occorrenze evidenziate al massimo nel testo completo
OCCURRENCES_SHOWN = 50      # brani mostrati in «Occorrenze»


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


def hit_spans(content: str, terms: list[str], limit: int = MAX_HITS) -> list[tuple[int, int]]:
    """Posizioni (inizio, fine) di tutte le occorrenze nel testo, nell'ordine; insensibile agli accenti.
    Il testo si legge a blocchi (non si duplica per intero) e si ferma a `limit` occorrenze."""
    rx = _term_regex(terms)
    if rx is None or not content:
        return []
    out: list[tuple[int, int]] = []
    chunk_size, overlap, offset, n, last_end = 100_000, 64, 0, len(content), 0
    while offset < n and len(out) < limit:
        chunk = content[offset:offset + chunk_size]
        norm, pos = _norm_with_map(chunk)
        for m in rx.finditer(norm):
            oa, ob = _map_span(chunk, pos, m.start(), m.end())
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


def page_for(starts: list[int], offset: int) -> int | None:
    """Pagina (da 1) che contiene `offset`, date le posizioni di inizio delle pagine; None se non ce ne sono."""
    return bisect_right(starts, offset) or None if starts else None


def occurrences(content: str, terms: list[str], starts: list[int] | None = None, max_items: int = 12,
                width: int = 70, gap: int = 200) -> tuple[list[dict], int]:
    """Le occorrenze delle parole cercate come voci per l'elenco «Altre occorrenze».
    Restituisce (voci, numero di occorrenze nel documento). Ogni voce: `index` (posizione tra tutte le occorrenze,
    la stessa del testo completo nell'anteprima), `page` (o None) e `parts`: [(testo, "hit" | "")] con un po'
    di contesto, e `count` (quante occorrenze contiene). Occorrenze a meno di `gap` caratteri dalla voce precedente
    e nella stessa pagina stanno nella stessa voce."""
    spans = hit_spans(content, terms)
    items: list[dict] = []
    last_end, last_page = -gap, None
    for k, (a, b) in enumerate(spans):
        page = page_for(starts or [], a)
        if items and a - last_end < gap and page == last_page:
            items[-1]["count"] += 1
            last_end = b
            continue
        if len(items) >= max_items:
            break
        last_end, last_page = b, page
        lo, hi = max(0, a - width), min(len(content), b + width)
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
        items.append({"index": k, "page": page, "count": 1, "parts": [p for p in parts if p[0]]})
    return items, len(spans)


def full_text_parts(content: str, terms: list[str], limit: int = FULL_TEXT_LIMIT) -> list[tuple[str, str]]:
    """Il testo del documento (fino a `limit` caratteri) con tutte le occorrenze evidenziate, come (testo, tag)."""
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return [("Nessun testo estratto: il file è stato trovato solo per il nome.", "dim")]
    shown = text[:limit]
    parts: list[tuple[str, str]] = []
    pos = 0
    for a, b in hit_spans(shown, terms):
        parts += [(shown[pos:a], ""), (shown[a:b], "hit")]
        pos = b
    parts.append((shown[pos:], ""))
    if len(text) > limit:
        parts.append((f"\n\n… Il testo continua: qui si vedono i primi {thousands(limit)} caratteri su "
                      f"{thousands(len(text))}. «Copia testo» copia tutto il documento.", "dim"))
    return parts


def anchored_parts(content, start, end, full=False, limit=FULL_TEXT_LIMIT):
    """Anteprima posizionata sui caratteri del testo originale, anche oltre i primi 400.000."""
    a = max(0, min(start, len(content)))
    b = min(len(content), max(a + 1, end))
    if full:
        # Centriamo la finestra sul punto richiesto solo quando altrimenti resterebbe fuori.
        lo = min(max(0, len(content) - limit), max(0, a - limit // 2)) if b > limit else 0
        hi = min(len(content), max(lo + limit, b))
    else:
        lo, hi = max(0, a - 150), min(len(content), b + 150)
    parts = ([("…\n", "dim")] if lo else [])
    parts += [(content[lo:a], ""), (content[a:b], "hit"), (content[b:hi], "")]
    if hi < len(content):
        parts.append(("\n…", "dim"))
    return [(t.replace("\r\n", "\n").replace("\r", "\n"), tag) for t, tag in parts if t]


def excerpts(content: str, terms: list[str], width: int = 140, max_n: int = 8):
    """Brani del testo attorno alle occorrenze (insensibile agli accenti).
    Restituisce [(testo, [(inizio, fine)...])]."""
    rx = _term_regex(terms) if terms else None
    if rx is None:
        return [(content[:600].replace("\r\n", "\n").replace("\n", " ⏎ "), [])] if content else []
    out: list[tuple[str, list[tuple[int, int]]]] = []
    last_end = -1
    chunk_size = 100_000
    overlap = width + 64
    offset = 0
    n = len(content)
    while offset < n and len(out) < max_n:
        chunk = content[offset:offset + chunk_size]
        norm, pos = _norm_with_map(chunk)
        for m in rx.finditer(norm):
            oa, ob = _map_span(chunk, pos, m.start(), m.end())
            abs_a, abs_b = offset + oa, offset + ob
            if abs_a < last_end:
                continue
            a, b = max(0, abs_a - width), min(n, abs_b + width)
            piece = content[a:b].replace("\r\n", "\n").replace("\n", " ⏎ ")
            pnorm, ppos = _norm_with_map(piece)
            marks = [_map_span(piece, ppos, x.start(), x.end()) for x in rx.finditer(pnorm)]
            out.append((piece, marks))
            last_end = b
            if len(out) >= max_n:
                break
        if offset + chunk_size >= n:
            break
        offset += chunk_size - overlap
    return out


def snippet_parts(snippet: str) -> list[tuple[str, str]]:
    """Trasforma lo snippet già calcolato da FTS5 in parti (testo, tag) per l'anteprima.
    Vuoto se lo snippet non contiene occorrenze (ad esempio match solo sul nome del file)."""
    if not snippet or HIT_START not in snippet:
        return []
    parts: list[tuple[str, str]] = []
    # i puntini ci sono già nello snippet di FTS5, e solo dove il testo è tagliato: non se ne aggiungono altri
    for i, chunk in enumerate(snippet.replace("\r\n", "\n").replace("\n", " ⏎ ").split(HIT_START)):
        if i == 0:
            parts.append((chunk, ""))
            continue
        hit, _, rest = chunk.partition(HIT_END)
        parts += [(hit, "hit"), (rest, "")]
    parts.append(("\n", ""))
    return [p for p in parts if p[0]]


def preview_parts(content: str, terms: list[str], max_n: int = 8) -> list[tuple[str, str]]:
    """Anteprima a brani (le occorrenze, fino a `max_n`) come parti (testo, tag). Può essere lenta su testi
    molto lunghi: la finestra la chiama in un thread."""
    if not content.strip():
        return [("Nessun testo estratto: il file è stato trovato solo per il nome.", "dim")]
    ex = excerpts(content, terms, max_n=max_n)
    if not ex:
        return [("Nessuna occorrenza nel contenuto (trovato nel nome del file). Anteprima iniziale:\n\n", "dim"),
                (content[:800].replace("\r\n", "\n"), "")]
    parts: list[tuple[str, str]] = []
    for text, marks in ex:
        pos = 0
        parts.append(("…", "dim"))
        for a, b in marks:
            parts += [(text[pos:a], ""), (text[a:b], "hit")]
            pos = b
        parts += [(text[pos:], ""), ("…\n\n", "dim")]
    return parts


def read_page_starts(doc_id: int) -> list[int]:
    """Posizioni in cui cominciano le pagine del documento (vuoto se non ha pagine), con una connessione propria."""
    con = protection.connection(DB_PATH)
    try:
        return [r[0] for r in con.execute("SELECT start FROM pages WHERE doc_id=? ORDER BY n", (doc_id,))]
    finally:
        con.close()


def read_content(doc_id: int) -> str:
    """Testo indicizzato di un documento, letto con una connessione propria e di breve durata
    (la connessione della finestra non si può usare da un altro thread)."""
    con = protection.connection(DB_PATH)
    try:
        row = con.execute("SELECT content FROM fts WHERE rowid=?", (doc_id,)).fetchone()
    finally:
        con.close()
    return row[0] if row and row[0] else ""
