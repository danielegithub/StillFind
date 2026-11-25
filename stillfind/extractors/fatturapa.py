# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Fatture elettroniche FatturaPA (.xml): testo leggibile invece dell'XML grezzo."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import date

from .text import _decode, extract_text_file

MAX_BYTES = 20_000_000


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, *path):
    for name in path:
        el = next((c for c in el if _local(c.tag) == name), None) if el is not None else None
    return el


def _children(el) -> list:
    return list(el) if el is not None else []


def _text(el, *path) -> str:
    node = _find(el, *path) if path else el
    return (node.text or "").strip() if node is not None else ""


def _party(el, label: str) -> list[str]:
    if el is None:
        return []
    an = _find(el, "DatiAnagrafici")
    name = _text(an, "Anagrafica", "Denominazione") or " ".join(
        x for x in (_text(an, "Anagrafica", "Nome"), _text(an, "Anagrafica", "Cognome")) if x)
    piva = " ".join(x for x in (_text(an, "IdFiscaleIVA", "IdPaese"), _text(an, "IdFiscaleIVA", "IdCodice")) if x)
    cf = _text(an, "CodiceFiscale")
    seat = _find(el, "Sede")
    addr = ", ".join(x for x in (_text(seat, "Indirizzo"), _text(seat, "NumeroCivico"), _text(seat, "CAP"),
                                 _text(seat, "Comune"), _text(seat, "Provincia")) if x)
    out = [f"{label}: {name}"]
    if piva:
        out.append(f"Partita IVA {label.lower()}: {piva}")
    if cf:
        out.append(f"Codice fiscale {label.lower()}: {cf}")
    if addr:
        out.append(f"Sede {label.lower()}: {addr}")
    return out


def parse_fattura(root, info=None) -> str:
    out: list[str] = []
    header = _find(root, "FatturaElettronicaHeader")
    out += _party(_find(header, "CedentePrestatore"), "Cedente")
    out += _party(_find(header, "CessionarioCommittente"), "Cessionario")
    for body in (c for c in root if _local(c.tag) == "FatturaElettronicaBody"):
        gen = _find(body, "DatiGenerali", "DatiGeneraliDocumento")
        unit = _text(gen, "Divisa") or "EUR"
        if info is not None and "document_date" not in info:
            try:
                info["document_date"] = date.fromisoformat(_text(gen, "Data")).isoformat(), "FatturaPA"
            except ValueError:
                pass
        out.append(f"Fattura numero {_text(gen, 'Numero')} del {_text(gen, 'Data')} "
                   f"({_text(gen, 'TipoDocumento')}), importo totale {_text(gen, 'ImportoTotaleDocumento')} "
                   f"{unit}".strip())
        for causale in (c for c in _children(gen) if _local(c.tag) == "Causale"):
            out.append("Causale: " + (causale.text or "").strip())
        for line in _children(_find(body, "DatiBeniServizi")):
            if _local(line.tag) == "DettaglioLinee":
                out.append(f"Riga {_text(line, 'NumeroLinea')}: {_text(line, 'Descrizione')} - quantità "
                           f"{_text(line, 'Quantita')} x {_text(line, 'PrezzoUnitario')} {unit} = "
                           f"{_text(line, 'PrezzoTotale')} {unit} (IVA {_text(line, 'AliquotaIVA')}%)")
            elif _local(line.tag) == "DatiRiepilogo":
                out.append(f"Riepilogo IVA {_text(line, 'AliquotaIVA')}%: imponibile "
                           f"{_text(line, 'ImponibileImporto')} {unit}, imposta {_text(line, 'Imposta')} {unit}")
        for pay in (c for c in body if _local(c.tag) == "DatiPagamento"):
            for det in pay:
                if _local(det.tag) == "DettaglioPagamento":
                    out.append(f"Pagamento: {_text(det, 'ImportoPagamento')} {unit} entro {_text(det, 'DataScadenzaPagamento')}"
                               + (f", IBAN {_text(det, 'IBAN')}" if _text(det, "IBAN") else ""))
    return "\n".join(out)


def extract_xml(path: str, info=None) -> str:
    """FatturaPA -> testo leggibile; qualunque altro XML resta testo semplice."""
    p = Path(path)
    if p.stat().st_size <= MAX_BYTES:
        try:
            root = ET.fromstring(_decode(p.read_bytes()).lstrip("\ufeff").encode("utf-8"))
            if _local(root.tag) == "FatturaElettronica":
                return parse_fattura(root, info)
        except (ET.ParseError, ValueError):
            pass
    return extract_text_file(path)
