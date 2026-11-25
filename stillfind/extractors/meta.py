# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Metadati dei documenti (titolo, autore, oggetto) da PDF, Office e OpenDocument.

Il testo dei metadati si indicizza insieme al contenuto, ma non conta per decidere se un file ha testo."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

from stillfind.extractors.office import OfficePackage


class MetadataReader:
    """Legge titolo, autore, oggetto e data di creazione dai formati che li hanno."""

    LABELS = (("title", "Titolo"), ("author", "Autore"), ("subject", "Oggetto"))
    OFFICE = {"title": "title", "creator": "author", "subject": "subject"}
    ODF = {"title": "title", "creator": "author", "initial-creator": "author", "subject": "subject"}

    @classmethod
    def _format(cls, values: dict[str, str]) -> str:
        return "\n".join(f"{label}: {values[key]}" for key, label in cls.LABELS if values.get(key))

    @staticmethod
    def _from_zip(path: str, member: str, tags: dict[str, str]) -> dict[str, str]:
        with OfficePackage(path) as z:
            root = ET.fromstring(z.read_limited(member, 1_000_000))
        out: dict[str, str] = {}
        for el in root.iter():
            key = tags.get(OfficePackage.local(el.tag))
            text = (el.text or "").strip()
            if key and text and key not in out:
                out[key] = text
        return out

    @classmethod
    def _from_pdf(cls, path: str) -> dict[str, str]:
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            return {}
        m = reader.metadata
        if not m:
            return {}
        values = {k: (getattr(m, k, None) or "").strip() for k, _ in cls.LABELS}
        try:
            if m.creation_date:
                values["created"] = m.creation_date.date().isoformat()
        except (ValueError, TypeError, AttributeError):
            pass
        return values

    @classmethod
    def read(cls, path: str, ext: str, info=None) -> str:
        """Testo «Titolo: … Autore: … Oggetto: …», vuoto se il formato non ha metadati o non sono leggibili."""
        try:
            if ext == "pdf":
                values = cls._from_pdf(path)
            elif ext in ("docx", "xlsx", "pptx"):
                values = cls._from_zip(path, "docProps/core.xml", {**cls.OFFICE, "created": "created"})
            elif ext in ("odt", "ods", "odp"):
                values = cls._from_zip(path, "meta.xml", {**cls.ODF, "creation-date": "created"})
            else:
                return ""
            if info is not None and values.get("created"):
                try:
                    info["metadata_date"] = date.fromisoformat(values["created"][:10]).isoformat()
                except ValueError:
                    pass
            return cls._format(values)
        except Exception:
            pass  # i metadati sono un di più: un file con metadati rovinati si indicizza lo stesso
        return ""
