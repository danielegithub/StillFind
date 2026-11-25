# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Fatture elettroniche FatturaPA (.xml): testo leggibile invece dell'XML grezzo."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from stillfind.extractors.base import ExtractionRequest, Extractor
from stillfind.extractors.text import PlainTextExtractor, TextDecoder


class FatturaPaExtractor(Extractor):
    """FatturaPA -> testo leggibile; qualunque altro XML resta testo semplice."""

    extensions = ("xml",)
    MAX_BYTES = 20_000_000

    @staticmethod
    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    @classmethod
    def _find(cls, el, *path):
        for name in path:
            el = next((c for c in el if cls._local(c.tag) == name), None) if el is not None else None
        return el

    @staticmethod
    def _children(el) -> list:
        return list(el) if el is not None else []

    @classmethod
    def _text(cls, el, *path) -> str:
        node = cls._find(el, *path) if path else el
        return (node.text or "").strip() if node is not None else ""

    @classmethod
    def _party(cls, el, label: str) -> list[str]:
        if el is None:
            return []
        t = cls._text
        an = cls._find(el, "DatiAnagrafici")
        name = t(an, "Anagrafica", "Denominazione") or " ".join(
            x for x in (t(an, "Anagrafica", "Nome"), t(an, "Anagrafica", "Cognome")) if x)
        piva = " ".join(x for x in (t(an, "IdFiscaleIVA", "IdPaese"), t(an, "IdFiscaleIVA", "IdCodice")) if x)
        cf = t(an, "CodiceFiscale")
        seat = cls._find(el, "Sede")
        addr = ", ".join(x for x in (t(seat, "Indirizzo"), t(seat, "NumeroCivico"), t(seat, "CAP"),
                                     t(seat, "Comune"), t(seat, "Provincia")) if x)
        out = [f"{label}: {name}"]
        if piva:
            out.append(f"Partita IVA {label.lower()}: {piva}")
        if cf:
            out.append(f"Codice fiscale {label.lower()}: {cf}")
        if addr:
            out.append(f"Sede {label.lower()}: {addr}")
        return out

    @classmethod
    def parse(cls, root, info=None) -> str:
        t, find, children, local = cls._text, cls._find, cls._children, cls._local
        out: list[str] = []
        header = find(root, "FatturaElettronicaHeader")
        out += cls._party(find(header, "CedentePrestatore"), "Cedente")
        out += cls._party(find(header, "CessionarioCommittente"), "Cessionario")
        for body in (c for c in root if local(c.tag) == "FatturaElettronicaBody"):
            gen = find(body, "DatiGenerali", "DatiGeneraliDocumento")
            unit = t(gen, "Divisa") or "EUR"
            if info is not None and "document_date" not in info:
                try:
                    info["document_date"] = date.fromisoformat(t(gen, "Data")).isoformat(), "FatturaPA"
                except ValueError:
                    pass
            out.append(f"Fattura numero {t(gen, 'Numero')} del {t(gen, 'Data')} "
                       f"({t(gen, 'TipoDocumento')}), importo totale {t(gen, 'ImportoTotaleDocumento')} "
                       f"{unit}".strip())
            for causale in (c for c in children(gen) if local(c.tag) == "Causale"):
                out.append("Causale: " + (causale.text or "").strip())
            for line in children(find(body, "DatiBeniServizi")):
                if local(line.tag) == "DettaglioLinee":
                    out.append(f"Riga {t(line, 'NumeroLinea')}: {t(line, 'Descrizione')} - quantità "
                               f"{t(line, 'Quantita')} x {t(line, 'PrezzoUnitario')} {unit} = "
                               f"{t(line, 'PrezzoTotale')} {unit} (IVA {t(line, 'AliquotaIVA')}%)")
                elif local(line.tag) == "DatiRiepilogo":
                    out.append(f"Riepilogo IVA {t(line, 'AliquotaIVA')}%: imponibile "
                               f"{t(line, 'ImponibileImporto')} {unit}, imposta {t(line, 'Imposta')} {unit}")
            for pay in (c for c in body if local(c.tag) == "DatiPagamento"):
                for det in pay:
                    if local(det.tag) == "DettaglioPagamento":
                        out.append(f"Pagamento: {t(det, 'ImportoPagamento')} {unit} entro {t(det, 'DataScadenzaPagamento')}"
                                   + (f", IBAN {t(det, 'IBAN')}" if t(det, "IBAN") else ""))
        return "\n".join(out)

    def extract(self, request: ExtractionRequest) -> tuple[str, bool]:
        p = Path(request.path)
        if p.stat().st_size <= self.MAX_BYTES:
            try:
                root = ET.fromstring(TextDecoder.decode(p.read_bytes()).lstrip("﻿").encode("utf-8"))
                if self._local(root.tag) == "FatturaElettronica":
                    return self.parse(root, request.info), False
            except (ET.ParseError, ValueError):
                pass
        return PlainTextExtractor.read(request.path), False
