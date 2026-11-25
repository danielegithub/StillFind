# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Interpretazione di ciò che l'utente scrive: testo da cercare, filtri e query FTS5."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

from stillfind.core import facts
from stillfind.core.labels import CATEGORIES
from stillfind.core.stemmer import stem

# «Cerca in»: dove si cerca. Le radici delle parole stanno in `stem` (contenuto) e `nstem` (nome).
WHERE_ALL, WHERE_CONTENT, WHERE_NAME = "all", "content", "name"


class SearchFilters(dict):
    """Filtri scritti nella ricerca (`ext`, `after`, `before`, `in`, `entities`, `amounts`) e loro SQL."""

    _DATE = re.compile(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$")

    @classmethod
    def date_start(cls, value: str) -> float | None:
        """Inizio del periodo (anno, mese o giorno) come timestamp locale; None se la data non è valida."""
        m = cls._DATE.match(value)
        if not m:
            return None
        try:
            return datetime(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)).timestamp()
        except ValueError:
            return None

    @classmethod
    def date_end(cls, value: str) -> float | None:
        """Fine del periodo (esclusa): before:2024-12 comprende tutto dicembre 2024."""
        m = cls._DATE.match(value)
        if not m or cls.date_start(value) is None:
            return None
        y, mo, d = int(m.group(1)), m.group(2), m.group(3)
        if d:
            return (datetime(y, int(mo), int(d)) + timedelta(days=1)).timestamp()
        if mo:
            return datetime(y + (int(mo) == 12), int(mo) % 12 + 1, 1).timestamp()
        return datetime(y + 1, 1, 1).timestamp()

    def fact_clauses(self):
        """Per ogni filtro su codici o importi: (clausola SQL, argomenti, alias della tabella delle posizioni)."""
        for kind, value in self.get("entities", []):
            yield ("FROM entita e JOIN menzioni m ON m.entita_id=e.id JOIN passaggi p ON p.id=m.passaggio_id "
                   "WHERE e.tipo=? AND e.valore_normalizzato=?", (kind, value), "m")
        for low, high in self.get("amounts", []):
            yield ("FROM importi i JOIN passaggi p ON p.id=i.passaggio_id "
                   "WHERE i.valuta='EUR' AND i.centesimi BETWEEN ? AND ?", (low, high), "i")

    def sql(self) -> tuple[str, list]:
        sql, args = "", []
        if "after" in self:
            sql += " AND d.mtime >= ?"
            args.append(self["after"])
        if "before" in self:
            sql += " AND d.mtime < ?"
            args.append(self["before"])
        if "in" in self:
            # una cartella con quel nome in qualunque punto del percorso (anche sottocartelle)
            def esc(t: str) -> str:
                return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            name, sep = esc(self["in"].strip("/\\")), esc(os.sep)
            sql += " AND (d.folder LIKE ? ESCAPE '\\' OR d.folder LIKE ? ESCAPE '\\')"
            args += [f"%{sep}{name}", f"%{sep}{name}{sep}%"]
        for clause, values, _ in self.fact_clauses():
            sql += f" AND d.id IN (SELECT p.doc_id {clause})"
            args.extend(values)
        return sql, args

    def document_clause(self, ext=None, under: str | None = None) -> tuple[str, list]:
        """Condizione SQL su `docs d` per filtri, tipo di file e cartella."""
        sql, args = self.sql()
        esql, eargs = self.ext_sql(ext)
        if under:
            sql += " AND (d.folder=? OR d.folder LIKE ? ESCAPE '\\')"
            args += list(self.under_clause(under))
        return sql + esql, args + eargs

    @staticmethod
    def under_clause(under: str) -> tuple[str, str]:
        clean = under.rstrip("/\\") or under
        like = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sep = os.sep.replace("\\", "\\\\")
        return clean, (like if clean in ("/", "\\") else like + sep) + "%"

    @staticmethod
    def ext_sql(ext) -> tuple[str, list]:
        """Filtro sul tipo: una sola estensione («pdf») oppure un gruppo (["docx", "odt"])."""
        if not ext:
            return "", []
        if isinstance(ext, str):
            return " AND d.ext = ?", [ext]
        ext = list(ext)
        return f" AND d.ext IN ({','.join('?' * len(ext))})", ext


class Query:
    """Una ricerca dell'utente: testo (senza i filtri) più i filtri, con le sue forme FTS5."""

    _RAW = re.compile(r'["*^]|\b(?:AND|OR|NOT|NEAR)\b|\b(?:name|content|stem|nstem):')
    OPERATORS = {"AND", "OR", "NOT", "NEAR"}
    _COLUMN_FILTER = re.compile(r"\b(?:name|content|stem|nstem):")
    _WHERE_COLUMNS = {WHERE_CONTENT: "{content stem}", WHERE_NAME: "{name nstem}"}
    _RAW_COLUMNS = {WHERE_ALL: "{name content}", WHERE_CONTENT: "{content}", WHERE_NAME: "{name}"}
    # codici e numeri con separatori interni (PR-00124/2025, 10:30, 12.500,00): il tokenizer li spezza,
    # quindi si cercano come frase, cioè parti vicine e nello stesso ordine
    _COMPOUND = re.compile(r"\w+(?:[-/.:,]+\w+)+")
    # filtri scritti nella ricerca: ext:pdf  after:2024-01  before:2024-12-31  in:Fatture  in:"Fatture 2025"
    _FILTER = re.compile(r'(?i)(?<!\S)(ext|tipo|after|before|in|cf|piva|iban|protocollo|email|importo):(?:"([^"]+)"|(\S+))'
                         r'|"[^"]*(?:"|$)')
    _BARE_AMOUNT = re.compile(r"(?<!\S)(?:~[+-]?\d[\d,.]*|[+-]?\d[\d,.]*\.\.[+-]?\d[\d,.]*)(?!\S)")
    # forma breve «atto notarile:pdf»: due punti e un tipo di file noto subito dopo una parola
    _KNOWN_EXTS = sorted({e for _, group in CATEGORIES for e in group}, key=len, reverse=True)
    _SHORT_EXT = re.compile(r"(?i)\b([^\W\d_]+):(" + "|".join(_KNOWN_EXTS) + r")(?!\w)")
    _RESERVED = {"ext", "tipo", "after", "before", "in", "name", "content", "stem", "nstem", "importo",
                 *facts.ENTITY_LABELS}

    def __init__(self, text: str, filters: SearchFilters | dict | None = None):
        self.text = text
        self.filters = SearchFilters(filters or {})

    def __repr__(self) -> str:
        return f"Query({self.text!r}, {dict(self.filters)!r})"

    # ---- lettura del testo scritto dall'utente
    @classmethod
    def parse(cls, raw: str) -> "Query":
        """Separa i filtri dalla ricerca. Un filtro non valido (after:ieri) resta nel testo da cercare."""
        flt = SearchFilters()

        def short_ext(m: re.Match) -> str:
            if m.group(1).lower() in cls._RESERVED:
                return m.group(0)
            flt["ext"] = m.group(2).lower()
            return m.group(1)  # resta la parola, il tipo diventa filtro

        def take(m: re.Match) -> str:
            if m.group(1) is None:  # l'intera frase tra virgolette è letterale
                return m[0]
            key, value = m.group(1).lower(), (m.group(2) or m.group(3)).strip()
            if key in ("ext", "tipo"):
                flt["ext"] = value.lower().lstrip(".")
            elif key == "after" and SearchFilters.date_start(value) is not None:
                flt["after"] = SearchFilters.date_start(value)
            elif key == "before" and SearchFilters.date_end(value) is not None:
                flt["before"] = SearchFilters.date_end(value)
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

        raw = cls._FILTER.sub(take, raw)
        # Le espressioni tra virgolette restano frasi letterali.
        parts = raw.split('"')
        parts[::2] = [cls._BARE_AMOUNT.sub(take_amount, cls._SHORT_EXT.sub(short_ext, p)) for p in parts[::2]]
        return cls(re.sub(r"\s+", " ", '"'.join(parts)).strip(), flt)

    # ---- proprietà
    @property
    def is_raw(self) -> bool:
        """Usa operatori, virgolette o filtri colonna di FTS5."""
        return bool(self._RAW.search(self.text))

    def plain_text(self) -> str:
        """Il testo se è una ricerca semplice; vuoto con operatori, virgolette o soli filtri
        (in quei casi la ricerca per significato non si applica)."""
        return "" if not self.text or self.is_raw else self.text

    def terms(self) -> list[str]:
        """Parole da evidenziare nei risultati, dalle più lunghe."""
        q = re.sub(r"\bNOT\s+\w+", " ", self.text)  # le parole escluse non si evidenziano
        q = re.sub(r"\b(?:name|content|stem|nstem):", " ", q)  # toglie i filtri colonna reali
        found = [w for w in re.findall(r"\w+", q) if w not in self.OPERATORS]
        return sorted(set(found), key=len, reverse=True)

    # ---- query FTS5
    def fts(self, where: str = WHERE_ALL) -> str:
        """Parole semplici -> tutte presenti, anche come prefisso (contr -> contratto).
        `where`: «all» (nome e contenuto), «content» o «name».
        Se compaiono operatori FTS5, virgolette o filtri colonna si usa la sintassi FTS5.
        Senza un filtro colonna esplicito la ricerca resta su nome e contenuto: le colonne
        `stem` e `nstem` hanno le radici in ordine alfabetico e farebbero trovare frasi in ordine diverso."""
        q = self.text.strip()
        if not q:
            return q
        if self.is_raw:
            if self._COLUMN_FILTER.search(q):
                return q
            return self._RAW_COLUMNS.get(where, self._RAW_COLUMNS[WHERE_ALL]) + " : (" + q + ")"
        return self.simple_fts(where)

    def simple_fts(self, where: str = WHERE_ALL) -> str:
        """Forma «semplice» (parole con prefisso e radice), anche quando il testo contiene operatori."""
        q = self.text
        parts = []
        for c in self._COMPOUND.findall(q):
            parts.append('"' + " ".join(re.findall(r"\w+", c)) + '"')
        rest = self._COMPOUND.sub(" ", q)
        words = [w for w in re.findall(r"\w+", rest) if w not in self.OPERATORS]
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
                parts.append(f'("{w}"{star} OR ' + (s_term if where in self._WHERE_COLUMNS
                                                   else f"{{stem nstem}} : {s_term}") + ")")
            else:
                parts.append(f'("{w}"{star})')
        expr = " AND ".join(parts)
        cols = self._WHERE_COLUMNS.get(where)
        return f"{cols} : ({expr})" if cols and expr else expr

    def passage_fts(self) -> str:
        """La corrispondenza FTS deve ricadere in un singolo passaggio strutturale."""
        if self.is_raw or len(re.findall(r"\w+", self._COMPOUND.sub(" codice ", self.text))) < 2:
            return ""
        return self.simple_fts(WHERE_CONTENT)
