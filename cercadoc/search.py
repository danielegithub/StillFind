# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Costruzione delle query FTS5 e ricerca."""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta

from . import facts
from .stemmer import stem, strip_accents
from .ui_text import CATEGORIES


HIT_START, HIT_END = "\x01", "\x02"  # marcatori usati da snippet()


_RAW = re.compile(r'["*^]|\b(?:AND|OR|NOT|NEAR)\b|\b(?:name|content|stem|nstem):')
_OPS = {"AND", "OR", "NOT", "NEAR"}


_COLUMN_FILTER = re.compile(r"\b(?:name|content|stem|nstem):")

# «Cerca in»: dove si cerca. Le radici delle parole stanno in `stem` (contenuto) e `nstem` (nome).
WHERE_ALL, WHERE_CONTENT, WHERE_NAME = "all", "content", "name"
_WHERE_COLUMNS = {WHERE_CONTENT: "{content stem}", WHERE_NAME: "{name nstem}"}
_RAW_COLUMNS = {WHERE_ALL: "{name content}", WHERE_CONTENT: "{content}", WHERE_NAME: "{name}"}
# codici e numeri con separatori interni (PR-00124/2025, 10:30, 12.500,00): il tokenizer li spezza,
# quindi si cercano come frase, cioè parti vicine e nello stesso ordine
_COMPOUND = re.compile(r"\w+(?:[-/.:,]+\w+)+")


def _simple_query(q: str, where: str = WHERE_ALL) -> str:
    parts = []
    for c in _COMPOUND.findall(q):
        parts.append('"' + " ".join(re.findall(r"\w+", c)) + '"')
    rest = _COMPOUND.sub(" ", q)
    words = [w for w in re.findall(r"\w+", rest) if w not in _OPS]
    if not words and not parts:
        words = re.findall(r"\w+", q)
    for w in words:
        if w.isdecimal():
            # numeri puri: corrispondenza esatta (123 non trova 1234); il prefisso solo con 123*
            parts.append(f'"{w}"')
            continue
        s = stem(w)
        # il prefisso solo da 3 lettere: «a*» o «co*» corrispondono a mezzo archivio e rallentano tutto
        star = "*" if len(w) >= 3 else ""
        if s and s != w.lower() and len(w) >= 3:
            s_term = f'"{s}"{"*" if len(s) >= 3 else ""}'
            # ovunque: le radici si cercano nelle due colonne; altrimenti ci pensa il filtro colonna di sotto
            parts.append(f'("{w}"{star} OR ' + (s_term if where in _WHERE_COLUMNS else f"{{stem nstem}} : {s_term}") + ")")
        else:
            parts.append(f'("{w}"{star})')
    expr = " AND ".join(parts)
    cols = _WHERE_COLUMNS.get(where)
    return f"{cols} : ({expr})" if cols and expr else expr


# «stesso passaggio»: le parole cercate devono stare entro questo numero di parole l'una dall'altra
NEAR_DISTANCE = 30


def _proximity_query(q: str) -> str:
    """Query FTS5 con le parole cercate vicine tra loro nel contenuto (stesso passaggio), usata solo
    per dare precedenza a quei documenti. Vuota se la ricerca non è semplice o ha una sola parola."""
    q = q.strip()
    if not q or _RAW.search(q):
        return ""
    units = ['"' + " ".join(re.findall(r"\w+", c)) + '"' for c in _COMPOUND.findall(q)]
    words = [w for w in re.findall(r"\w+", _COMPOUND.sub(" ", q)) if w not in _OPS]
    for w in words:
        if w.isdecimal():
            units.append(f'"{w}"')
        else:
            s = stem(w)
            units.append(f'"{s}"*' if len(s) >= 3 else f'"{w}"')
    if len(units) < 2:
        return ""
    return "content : NEAR(" + " ".join(units) + f", {NEAR_DISTANCE})"


# filtri scritti nella ricerca: ext:pdf  after:2024-01  before:2024-12-31  in:Fatture  in:"Fatture 2025"
_FILTER = re.compile(r'(?i)(?<!\S)(ext|tipo|after|before|in|cf|piva|iban|protocollo|email|importo):(?:"([^"]+)"|(\S+))'
                     r'|"[^"]*(?:"|$)')
_BARE_AMOUNT = re.compile(r"(?<!\S)(?:~[+-]?\d[\d,.]*|[+-]?\d[\d,.]*\.\.[+-]?\d[\d,.]*)(?!\S)")
# forma breve «atto notarile:pdf»: due punti e un tipo di file noto subito dopo una parola
_KNOWN_EXTS = sorted({e for _, group in CATEGORIES for e in group}, key=len, reverse=True)
_SHORT_EXT = re.compile(r"(?i)\b([^\W\d_]+):(" + "|".join(_KNOWN_EXTS) + r")(?!\w)")
_RESERVED = {"ext", "tipo", "after", "before", "in", "name", "content", "stem", "nstem", "importo", *facts.ENTITY_LABELS}
_DATE = re.compile(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$")


def _date_start(value: str) -> float | None:
    """Inizio del periodo (anno, mese o giorno) come timestamp locale; None se la data non è valida."""
    m = _DATE.match(value)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)).timestamp()
    except ValueError:
        return None


def _date_end(value: str) -> float | None:
    """Fine del periodo (esclusa): before:2024-12 comprende tutto dicembre 2024."""
    m = _DATE.match(value)
    if not m or _date_start(value) is None:
        return None
    y, mo, d = int(m.group(1)), m.group(2), m.group(3)
    if d:
        return (datetime(y, int(mo), int(d)) + timedelta(days=1)).timestamp()
    if mo:
        return datetime(y + (int(mo) == 12), int(mo) % 12 + 1, 1).timestamp()
    return datetime(y + 1, 1, 1).timestamp()


def parse_filters(q: str) -> tuple[str, dict]:
    """Separa i filtri dalla ricerca. Un filtro non valido (after:ieri) resta nel testo da cercare."""
    flt: dict = {}

    def short_ext(m: re.Match) -> str:
        if m.group(1).lower() in _RESERVED:
            return m.group(0)
        flt["ext"] = m.group(2).lower()
        return m.group(1)  # resta la parola, il tipo diventa filtro

    def take(m: re.Match) -> str:
        if m.group(1) is None:  # l'intera frase tra virgolette è letterale
            return m[0]
        key, value = m.group(1).lower(), (m.group(2) or m.group(3)).strip()
        if key in ("ext", "tipo"):
            flt["ext"] = value.lower().lstrip(".")
        elif key == "after" and _date_start(value) is not None:
            flt["after"] = _date_start(value)
        elif key == "before" and _date_end(value) is not None:
            flt["before"] = _date_end(value)
        elif key == "in" and value:
            flt["in"] = value
        elif key in facts.ENTITY_LABELS:
            normalized = value.casefold() if key == "email" else "".join(value.upper().split())
            if key == "piva":
                normalized = normalized.removeprefix("IT")
            flt.setdefault("entities", []).append((key, normalized))
        elif key == "importo":
            limits = facts.amount_range(value)
            if limits is None:
                return m.group(0)
            flt.setdefault("amounts", []).append(limits)
        else:
            return m.group(0)
        return " "

    def take_amount(m):
        limits = facts.amount_range(m[0])
        if limits is None:
            return m[0]
        flt.setdefault("amounts", []).append(limits)
        return " "

    q = _FILTER.sub(take, q)
    # Le espressioni tra virgolette restano frasi letterali.
    parts = q.split('"')
    parts[::2] = [_BARE_AMOUNT.sub(take_amount, _SHORT_EXT.sub(short_ext, p)) for p in parts[::2]]
    return re.sub(r"\s+", " ", '"'.join(parts)).strip(), flt


def _fact_filters(flt):
    for kind, value in flt.get("entities", []):
        yield ("FROM entita e JOIN menzioni m ON m.entita_id=e.id JOIN passaggi p ON p.id=m.passaggio_id "
               "WHERE e.tipo=? AND e.valore_normalizzato=?", (kind, value), "m")
    for low, high in flt.get("amounts", []):
        yield ("FROM importi i JOIN passaggi p ON p.id=i.passaggio_id "
               "WHERE i.valuta='EUR' AND i.centesimi BETWEEN ? AND ?", (low, high), "i")


def _filter_sql(flt: dict) -> tuple[str, list]:
    sql, args = "", []
    if "after" in flt:
        sql += " AND d.mtime >= ?"
        args.append(flt["after"])
    if "before" in flt:
        sql += " AND d.mtime < ?"
        args.append(flt["before"])
    if "in" in flt:
        # una cartella con quel nome in qualunque punto del percorso (anche sottocartelle)
        def esc(t: str) -> str:
            return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        name, sep = esc(flt["in"].strip("/\\")), esc(os.sep)
        sql += " AND (d.folder LIKE ? ESCAPE '\\' OR d.folder LIKE ? ESCAPE '\\')"
        args += [f"%{sep}{name}", f"%{sep}{name}{sep}%"]
    for clause, values, _ in _fact_filters(flt):
        sql += f" AND d.id IN (SELECT p.doc_id {clause})"
        args.extend(values)
    return sql, args


def document_filter(flt, ext=None, under=None):
    sql, args = _filter_sql(flt)
    esql, eargs = _ext_sql(ext)
    if under:
        sql += " AND (d.folder=? OR d.folder LIKE ? ESCAPE '\\')"
        args += list(_under_clause(under))
    return sql + esql, args + eargs


def _show_fact_matches(con, rows, flt):
    first = next(_fact_filters(flt), None)
    if first:
        clause, args, alias = first
        for row in rows:
            match = con.execute(f"SELECT {alias}.start,{alias}.end {clause} AND p.doc_id=? "
                                f"ORDER BY {alias}.start LIMIT 1", (*args, row["id"])).fetchone()
            if match:
                a, b = match
                left = max(0, a - 65)
                before, value, after = con.execute(
                    "SELECT substr(content,?,?),substr(content,?,?),substr(content,?,80) FROM fts WHERE rowid=?",
                    (left + 1, a - left, a + 1, b - a, b + 1, row["id"])).fetchone()
                row.update(offset=a, end=b, snippet=before + HIT_START + value + HIT_END + after)
    return rows


_DOC_DATE_ORDER = "coalesce(a.data_iso,date(d.mtime,'unixepoch','localtime')) DESC,d.name COLLATE NOCASE"
_DOC_KEYS = ("id", "path", "name", "folder", "ext", "size", "mtime", "snippet", "ocr", "error",
             "document_date", "date_source")
_DOC_SELECT = ("SELECT d.id,d.path,d.name,d.folder,d.ext,d.size,d.mtime,'',d.ocr,d.error,a.data_iso,a.fonte "
               "FROM docs d LEFT JOIN analisi a ON a.doc_id=d.id ")


def build_query(q: str, where: str = WHERE_ALL) -> str:
    """Parole semplici -> tutte presenti, anche come prefisso (contr -> contratto).
    `where`: «all» (nome e contenuto), «content» o «name».
    Se compaiono operatori FTS5, virgolette o filtri colonna si usa la sintassi FTS5.
    Senza un filtro colonna esplicito la ricerca resta su nome e contenuto: le colonne
    `stem` e `nstem` hanno le radici in ordine alfabetico e farebbero trovare frasi in ordine diverso."""
    q = q.strip()
    if not q:
        return q
    if _RAW.search(q):
        return q if _COLUMN_FILTER.search(q) else _RAW_COLUMNS.get(where, _RAW_COLUMNS[WHERE_ALL]) + " : (" + q + ")"
    return _simple_query(q, where)


def query_terms(q: str) -> list[str]:
    q = parse_filters(q)[0]
    q = re.sub(r"\bNOT\s+\w+", " ", q)  # le parole escluse non si evidenziano
    q = re.sub(r"\b(?:name|content|stem|nstem):", " ", q)  # toglie i filtri colonna reali
    terms = [w for w in re.findall(r"\w+", q) if w not in _OPS]
    return sorted(set(terms), key=len, reverse=True)


def _under_clause(under: str) -> tuple[str, str]:
    clean = under.rstrip("/\\") or under
    like = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    sep = os.sep.replace("\\", "\\\\")
    return clean, (like if clean in ("/", "\\") else like + sep) + "%"


def _ext_sql(ext) -> tuple[str, list]:
    """Filtro sul tipo: una sola estensione («pdf») oppure un gruppo (["docx", "odt"])."""
    if not ext:
        return "", []
    if isinstance(ext, str):
        return " AND d.ext = ?", [ext]
    ext = list(ext)
    return f" AND d.ext IN ({','.join('?' * len(ext))})", ext


def search(con, q: str, ext=None, order: str = "rank", limit: int = 500,
           under: str | None = None, after: float | None = None, before: float | None = None,
           where: str = WHERE_ALL) -> list[dict]:
    """`ext`: una estensione o un elenco; `after` e `before`: solo documenti modificati dopo / prima di questi
    timestamp (`before` escluso); `where`: «all» (nome e contenuto), «content» o «name»."""
    q, flt = parse_filters(q)
    ext = flt.get("ext", ext)
    typed = bool(flt)
    if after is not None:
        flt["after"] = max(after, flt.get("after", 0.0))
    if before is not None:
        flt["before"] = min(before, flt.get("before", before))
    if not q:
        # solo filtri (es. «ext:pdf after:2025»): elenco dei documenti che li rispettano
        return _show_fact_matches(con, list_docs(con, ext, order, limit, under, flt), flt) if typed else []
    match = build_query(q, where)
    if not match:
        return []
    fsql, fargs = document_filter(flt, ext, under)
    order_sql = {"rank": "bm25(fts, 8.0, 1.0, 0.3, 0.5)", "date": "d.mtime DESC",
                 "document_date": _DOC_DATE_ORDER, "name": "d.name COLLATE NOCASE"}[order]
    near = _proximity_query(q) if order == "rank" and where != WHERE_NAME else ""
    if near:
        # prima i documenti con le parole nello stesso passaggio, poi gli altri (sempre per rilevanza)
        order_sql = "(d.id IN (SELECT rowid FROM fts WHERE fts MATCH ?)) DESC, " + order_sql
    sql = f"""SELECT d.id, d.path, d.name, d.folder, d.ext, d.size, d.mtime,
                     snippet(fts, 1, '{HIT_START}', '{HIT_END}', '…', 20), d.ocr, d.error, a.data_iso, a.fonte
              FROM fts JOIN docs d ON d.id = fts.rowid LEFT JOIN analisi a ON a.doc_id=d.id
              WHERE fts MATCH ?{fsql}
              ORDER BY {order_sql} LIMIT ?"""
    args: list = [match, *fargs]
    if near:
        args.append(near)
    args += [limit]
    try:
        rows = con.execute(sql, args).fetchall()
    except con.OperationalError as ex:
        if "interrupt" in str(ex).lower():
            raise  # ricerca annullata dalla finestra perché ne è partita una più recente
        # sintassi FTS5 non valida: ripiega sulla ricerca semplice (mantenendo prefissi e stemming)
        safe = _simple_query(q, where)
        if not safe:
            return []
        args[0] = safe
        rows = con.execute(sql, args).fetchall()
    return [dict(zip(_DOC_KEYS, r)) for r in rows]


def page_at(con, doc_id: int, offset: int) -> int | None:
    """Numero della pagina del documento che contiene la posizione `offset` del testo; None se il documento
    non ha pagine (solo i PDF le hanno)."""
    row = con.execute("SELECT n FROM pages WHERE doc_id = ? AND start <= ? ORDER BY n DESC LIMIT 1",
                      (doc_id, max(0, offset))).fetchone()
    return row[0] if row else None


def add_pages(con, rows: list[dict], limit: int = 100) -> None:
    """Aggiunge a ogni risultato (i primi `limit`) la chiave `page`: la pagina del passaggio trovato, o None.
    La posizione del passaggio si ricava dall'estratto (che è un pezzo identico del testo) o, per i risultati
    trovati per significato, da `offset`. Si guarda solo nei documenti che hanno pagine."""
    for r in rows[:limit]:
        r["page"] = None
        if not con.execute("SELECT 1 FROM pages WHERE doc_id = ? LIMIT 1", (r["id"],)).fetchone():
            continue
        offset = r.get("offset")
        if offset is None:
            snip = r.get("snippet") or ""
            if HIT_START not in snip:
                continue  # trovato solo nel nome: nessun passaggio
            plain = snip.replace(HIT_START, "").replace(HIT_END, "")
            plain = plain[1:] if plain.startswith("…") else plain
            plain = plain[:-1] if plain.endswith("…") else plain
            row = con.execute("SELECT instr(content, ?) - 1 FROM fts WHERE rowid = ?", (plain, r["id"])).fetchone()
            offset = row[0] if row and row[0] is not None and row[0] >= 0 else None
        if offset is not None:
            r["page"] = page_at(con, r["id"], offset)


def plain_text_and_filters(q: str) -> tuple[str, dict]:
    """(testo, filtri) di una ricerca; il testo è vuoto se non è una ricerca semplice (operatori, virgolette,
    filtri colonna) o se contiene solo filtri: in quei casi la ricerca per significato non si applica."""
    text, flt = parse_filters(q)
    return ("" if not text or _RAW.search(text) else text), flt


def docs_by_id(con, ids, ext=None, under: str | None = None, flt: dict | None = None) -> dict[int, dict]:
    """Righe dei documenti indicati (nello stesso formato dei risultati), con gli stessi filtri della ricerca."""
    ids = list(ids)
    if not ids:
        return {}
    fsql, fargs = document_filter(flt or {}, ext, under)
    sql = _DOC_SELECT + f"WHERE d.id IN ({','.join('?' * len(ids))})" + fsql
    return {r[0]: dict(zip(_DOC_KEYS, r)) for r in con.execute(sql, ids + fargs)}


def list_docs(con, ext=None, order: str = "name", limit: int = 500,
              under: str | None = None, flt: dict | None = None) -> list[dict]:
    """Documenti indicizzati (di una cartella e sottocartelle) senza ricerca testuale."""
    fsql, fargs = document_filter(flt or {}, ext, under)
    order_sql = {"date": "d.mtime DESC", "document_date": _DOC_DATE_ORDER}.get(order, "d.name COLLATE NOCASE")
    sql = _DOC_SELECT + "WHERE 1=1" + fsql + f" ORDER BY {order_sql} LIMIT ?"
    return [dict(zip(_DOC_KEYS, r)) for r in con.execute(sql, [*fargs, limit])]


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


def suggest(con, q: str) -> str | None:
    """«Intendevi…?»: se una parola cercata non esiste nell'archivio, propone la parola più vicina tra quelle
    realmente presenti (tabella virtuale `fts5vocab`, nessun indice in più). None se non serve."""
    q = parse_filters(q)[0]
    q = re.sub(r"\b(?:name|content|stem|nstem):", " ", q)
    words = [w for w in re.findall(r"[^\W\d_]+", q) if w.upper() not in _OPS and len(w) >= 4]
    if not words:
        return None
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_vocab USING fts5vocab(main, fts, 'col')")
    result, changed = q, False
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
            d = _edit_distance(key, term, limit)
            if d <= limit and (best is None or (d, -doc) < (best[0], -best[1])):
                best = (d, doc, term)
        if best:
            result = re.sub(rf"\b{re.escape(w)}\b", best[2], result, count=1)
            changed = True
    return result.strip() if changed else None
