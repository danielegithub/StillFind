# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Entità, importi e date riconoscibili nel testo indicizzato. Gli intervalli sono in caratteri."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from stillfind.core.i18n import mark, tr

ENTITY_LABELS = {"cf": mark("Codici fiscali"), "piva": mark("Partite IVA"), "iban": mark("IBAN"),
                 "protocollo": mark("Protocolli e pratiche"), "email": mark("Email e PEC"),
                 "soggetto": mark("Cedenti e cessionari"), "messaggio": mark("Conversazioni email")}


def entity_label(kind: str, default: str = "") -> str:
    """Nome dell'entità nella lingua corrente; `ENTITY_LABELS` conserva le chiavi interne (cf, piva…)."""
    return tr(ENTITY_LABELS[kind]) if kind in ENTITY_LABELS else default
PROTOCOL_PREFIXES = ("PR", "PROT", "PRATICA", "PROTOCOLLO")
_OMOCODIA = "0-9LMNPQRSTUV"
_CF = re.compile(rf"(?i)(?<![a-z0-9])[a-z]{{6}}[{_OMOCODIA}]{{2}}[ABCDEHLMPRST][{_OMOCODIA}]{{2}}[a-z][{_OMOCODIA}]{{3}}[a-z](?![a-z0-9])")
_PIVA = re.compile(r"(?i)(?<!\w)(?:IT[ \t]*)?(\d{11})(?!\w)")
_IBAN = re.compile(r"(?<![A-Za-z0-9])(?=([A-Za-z]{2}\d{2}(?:[ \t]?[A-Za-z0-9]){11,30}))")
# Lunghezza nazionale: impedisce di inglobare la parola dopo un IBAN scritto a gruppi.
_IBAN_LENGTHS = dict(IT=27, SM=27, VA=22, DE=22, FR=27, GB=22, ES=24, CH=21, AT=20, BE=16,
                     NL=18, LU=20, IE=22, PT=25, GR=27, MT=31, FI=18, DK=18, NO=15, SE=24,
                     PL=28, CZ=24, SK=24, SI=19, HR=21, HU=28, RO=24, BG=22, CY=28, EE=20,
                     LV=21, LT=20, LI=21, IS=26, MC=27)
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.!#$%&'*+/=?^_`{|}~-]+@(?:[\w-]+\.)+[a-z]{2,63}\b")
_MSG_LINE = re.compile(r"(?im)^(ID messaggio|In risposta a|Riferimenti):[ \t]*([^\r\n]+)")
_PARTY = re.compile(r"(?im)^(Cedente|Cessionario):[ \t]*([^\r\n]+)")
_ODD = (1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23)


def valid_cf(value):
    value = value.upper()
    if not _CF.fullmatch(value):
        return False
    numeric = value.translate(str.maketrans("LMNPQRSTUV", "0123456789"))
    day = int(numeric[9:11])
    try:
        date(2000 + int(numeric[6:8]), "ABCDEHLMPRST".index(value[8]) + 1, day - 40 if day > 40 else day)
    except ValueError:
        return False
    nums = [int(c) if c.isdigit() else ord(c) - 65 for c in value[:-1]]
    return chr(65 + sum(n if i % 2 else _ODD[n] for i, n in enumerate(nums)) % 26) == value[-1]


def valid_piva(value):
    if not re.fullmatch(r"[0-9]{11}", value) or len(set(value)) == 1:
        return False
    values = [int(c) for c in value]
    return (sum(values[::2]) + sum(2 * n - 9 * (n >= 5) for n in values[1::2])) % 10 == 0


def valid_iban(value):
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", value):
        return False
    if len(value) != _IBAN_LENGTHS.get(value[:2], len(value)):
        return False
    digits = "".join(c if c.isdigit() else str(ord(c) - 55) for c in value[4:] + value[:4])
    return int(digits) % 97 == 1


def protocol_pattern(prefixes):
    names = sorted({str(p).strip().upper() for p in prefixes if str(p).strip()}, key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?i)(?<!\w)(" + "|".join(map(re.escape, names))
                      + r")[.\s:-]*(?:n[.°º]?\s*)?(\d{1,12})\s*[/\-]\s*(\d{4})(?!\w)")


def entities(text, enabled, protocols, *, invoice=False, mail=False):
    """(tipo, valore normalizzato, etichetta, ruolo, inizio, fine) per menzione."""
    for field, rx, check in (("cf", _CF, valid_cf), ("piva", _PIVA, valid_piva)):
        if field in enabled or field == "piva" and "cf" in enabled:
            for m in rx.finditer(text):
                value = (m[1] if field == "piva" else m[0]).upper()
                label = text[max(0, m.start() - 40):m.start()].lower()
                numeric_cf = field == "piva" and re.search(r"(?:codice fiscale|c\.?f\.?)"
                                                           r"(?: (?:cedente|cessionario))?[:\s]*$", label)
                kind = "cf" if numeric_cf else field
                if kind in enabled and check(value):
                    role = ""
                    if invoice:
                        label = label.rstrip(": ")
                        if label.endswith(("cedente", "cessionario")):
                            role = label.split()[-1]
                    yield kind, value, value, role, *m.span()
    if "iban" in enabled:
        for m in _IBAN.finditer(text):
            raw = m[1]
            positions = [i for i, ch in enumerate(raw) if ch not in " \t"]
            n = _IBAN_LENGTHS.get(raw[:2].upper())
            ends = [n] if n else range(15, len(positions) + 1)
            for size in ends:
                if size > len(positions):
                    continue
                end = m.start() + positions[size - 1] + 1
                if end < len(text) and text[end].isalnum():
                    continue
                value = re.sub(r"\s", "", text[m.start():end]).upper()
                if valid_iban(value):
                    yield "iban", value, value, "", m.start(), end
                    break
    if "protocollo" in enabled and protocols:
        for m in protocols.finditer(text):
            value = f"{m[1].upper()}-{m[2]}/{m[3]}"
            yield "protocollo", value, value, "", *m.span()
    header_ids = list(_MSG_LINE.finditer(text)) if mail else []
    if "messaggio" in enabled:
        for line in header_ids:
            role = {"id messaggio": "id", "in risposta a": "risposta", "riferimenti": "riferimento"}[line[1].lower()]
            for m in re.finditer(r"<([^<>\s]+)>", line[2]):
                yield "messaggio", m[1], m[1], role, line.start(2) + m.start(), line.start(2) + m.end()
    if "email" in enabled:
        for m in _EMAIL.finditer(text):
            if any(h.start() <= m.start() < h.end() for h in header_ids):
                continue
            role = ""
            if mail:
                header = text[text.rfind("\n", 0, m.start()) + 1:m.start()].partition(":")[0].lower()
                role = {"da": "mittente", "a": "destinatario", "cc": "destinatario"}.get(header, "")
            yield "email", m[0].casefold(), m[0], role, *m.span()
    if invoice and "soggetto" in enabled:
        for m in _PARTY.finditer(text):
            name = " ".join(m[2].split())
            if name:
                yield "soggetto", name.casefold(), name, m[1].lower(), *m.span(2)


_CCY = r"(?:€|EUR(?:O)?|USD|\$|GBP|£|CHF)"
_NUMBER = r"[+-]?(?:\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_AMOUNT = re.compile(rf"(?ix)(?<![\w/.,])(?:(?P<pre>{_CCY})\s*(?P<a>{_NUMBER})|"
                     rf"(?P<b>{_NUMBER})[ \t]*(?P<post>{_CCY})|(?P<plain>\d{{1,3}}(?:\.\d{{3}})+(?:,\d{{1,2}})?))"
                     r"(?!\w|[.,/]\d|\s*%)")
_CURRENCIES = {"€": "EUR", "EURO": "EUR", "$": "USD", "£": "GBP"}


def cents(raw):
    if not re.fullmatch(_NUMBER, raw) or len(raw) > 24:
        return None
    last = max(raw.rfind("."), raw.rfind(","))
    if last >= 0 and len(raw) - last <= 3:
        raw = re.sub(r"[.,]", "", raw[:last]) + "." + raw[last + 1:]
    else:
        raw = re.sub(r"[.,]", "", raw)
    try:
        value = int(Decimal(raw) * 100)
        return value if abs(value) <= 2**63 - 1 else None
    except (ValueError, InvalidOperation):
        return None


def amount_range(expr):
    if expr.startswith("~"):
        value = cents(expr[1:])
        if value is not None:
            delta = int((Decimal(abs(value)) / 20).quantize(1, rounding=ROUND_HALF_UP))
            return max(-2**63, value - delta), min(2**63 - 1, value + delta)
    parts = expr.split("..")
    if len(parts) in (1, 2):
        a, b = cents(parts[0]), cents(parts[-1])
        if a is not None and b is not None and a <= b:
            return a, b
    return None


def amounts(text, invoice_units=()):
    # FatturaPA usa sempre il punto decimale e fino a otto decimali per i prezzi unitari.
    # L'anteprima conserva anche le frazioni di centesimo presenti nell'originale.
    structured = []
    if invoice_units:
        codes = "|".join(map(re.escape, sorted(invoice_units)))
        for m in re.finditer(rf"(?<![\w.,/])([+-]?\d{{1,17}}(?:\.\d{{1,8}})?)[ \t]+({codes})\b", text):
            value = int((Decimal(m[1]) * 100).to_integral_value(rounding=ROUND_HALF_UP))
            structured.append(m.span())
            if abs(value) <= 2**63 - 1:
                yield value, m[2], *m.span()
    i = 0
    for m in _AMOUNT.finditer(text):
        while i < len(structured) and structured[i][1] <= m.start():
            i += 1
        if i < len(structured) and m.end() > structured[i][0]:
            continue
        unit = (m["pre"] or m["post"] or "EUR").upper()
        value = cents(m["a"] or m["b"] or m["plain"])
        if value is not None:
            yield value, _CURRENCIES.get(unit, unit), m.start(), m.end()


_MONTHS = "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre".split()
_EN_MONTHS = "january february march april may june july august september october november december".split()
_MONTH = {spelling: i for months in (_MONTHS, _EN_MONTHS) for i, m in enumerate(months, 1)
          for spelling in (m, m[:3])}
_DATE = re.compile(r"(?ix)(?<!\d)(?:(\d{4})-(\d{2})-(\d{2})|(\d{1,2})[./-](\d{1,2})[./-](\d{4})|"
                   r"(\d{1,2})\s+([a-z]{3,9})\.?\s+(\d{4}))(?!\d)")
_DUE = re.compile(r"(?i)\b(?:scad(?:e|rà|enza)|entro|rinnov[oa]|disdetta|valid[oa]\s+fino|validità\s+fino|termine)\b")
_DOC_DATE = re.compile(r"(?i)\b(?:data(?:\s+(?:del\s+documento|di\s+emissione|fattura))?\s*[:=]?|"
                       r"(?:fattura|contratto|verbale|lettera|preventivo)[^\n;]{0,65}?\bdel|"
                       r"(?:luogo e data|emess[oa]\s+il)|l[ìi],?)\s*$")


def dates(text):
    for m in _DATE.finditer(text):
        if m[1]:
            year, month, day = map(int, m.group(1, 2, 3))
        elif m[4]:
            day, month, year = map(int, m.group(4, 5, 6))
        else:
            day, month, year = int(m[7]), _MONTH.get(m[8].lower(), 0), int(m[9])
        try:
            yield date(year, month, day).isoformat(), m.start(), m.end()
        except ValueError:
            continue


def dated_facts(text, offset=0):
    """Una scadenza deve avere il suo indicatore nella stessa frase. Le date di emissione hanno priorità."""
    for iso, a, b in dates(text):
        lead = text[max(0, a - 120):a]
        clause = re.split(r"[;!?]|\.(?:\s|$)|\n\s*\n", lead)[-1]
        markers = list(_DUE.finditer(clause))
        if markers and len(clause) - markers[-1].end() <= 60 and not _DATE.search(clause[markers[-1].end():]):
            start = a - len(clause) + markers[-1].start()
            yield "scadenza", iso, start, b
        elif label := re.match(r"(?i)[ \t]*\([ \t]*(?:scadenza|rinnovo|disdetta|termine)[ \t]*\)", text[b:b+30]):
            yield "scadenza", iso, a, b + label.end()
        elif _DOC_DATE.search(lead) or re.search(r"(?i)(?:^|\n)Data:\s*(?:[A-Z]{3},\s*)?$", lead):
            yield "documento", iso, a, b
        elif offset + a < 500 and (re.search(r"(?:^|\n)[^\n\d]{2,40},\s*(?:il\s+)?$", lead) or
                                   not text[:a].strip() and offset == 0):
            yield "intestazione", iso, a, b
