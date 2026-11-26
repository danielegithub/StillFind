# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Motivi per cui un file non si legge, o si legge solo in parte."""
from __future__ import annotations

import re

from stillfind.core.i18n import mark, tr


class ReadProblems:
    """Nell'archivio il motivo resta un testo in italiano, come nelle versioni precedenti. Questa classe è l'unico
    punto che conosce quei testi: il resto dell'app chiede `kind()`, `is_partial()` e `display()`."""

    PARTIAL_MARK = "Lettura parziale"
    PARTIAL = PARTIAL_MARK + " · "   # il file ha comunque del testo cercabile
    PARTIAL_OCR = PARTIAL + "OCR,"   # alcune pagine non sono state lette con l'OCR

    # I messaggi che l'app scrive nell'archivio; `{nome}` è la parte che cambia e non si traduce.
    TEMPLATES = (
        mark("PDF protetto da password"),
        mark("File oltre il limite ({size} > {limit} MB)"),
        mark("Lettura parziale · OCR, pag. {page}: {detail}"),
        mark("Lettura parziale · {n} elementi non leggibili"),
        mark("Lettura parziale · {detail}"),
        mark("Tesseract non installato"),
        mark("Tesseract non disponibile."),
        mark("Tesseract errore ({code})"),
        mark("pdftoppm fallito"),
        mark("OCR dei PDF: serve pypdfium2, pdftoppm (Poppler) oppure PyMuPDF"),
        mark("per i PDF installa pypdf:  pip install pypdf"),
        mark("Contenuto interno troppo grande ({name})"),
        mark("Contenuto decompresso oltre il limite complessivo."),
        mark("Troppi elementi nel documento Office."),
        mark("Dichiarazione XML non supportata nel libro."),
        mark("Archivio con troppi elementi."),
        mark("Dimensione del membro non valida."),
        mark("Percorso EPUB non valido."),
        mark("Descrizione EPUB assente."),
        mark("file .p7m senza documento (firma separata o formato non riconosciuto)"),
        mark("documento contenuto oltre il limite di dimensione"),
        mark("tipo di documento contenuto non riconosciuto"),
    )
    # tipo di problema → parole che lo riconoscono nel testo salvato
    KINDS = (("password", "password"), ("limit", "oltre il limite"), ("pypdf", "pypdf"), ("tesseract", "tesseract"),
             ("pdftoppm", "pdftoppm"), ("partial", "lettura parziale"), ("p7m", ".p7m"),
             ("too_large", "troppo grande"))

    @staticmethod
    def _pattern(template: str) -> re.Pattern:
        parts = re.split(r"\{(\w+)\}", template)
        return re.compile("".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>.+?)" for i, p in enumerate(parts)), re.S)

    @classmethod
    def is_partial(cls, error: str | None) -> bool:
        return bool(error) and error.startswith(cls.PARTIAL_MARK)

    @classmethod
    def is_ocr_page_error(cls, error: str | None) -> bool:
        return bool(error) and error.startswith(cls.PARTIAL_OCR)

    @classmethod
    def display(cls, error: str | None) -> str:
        """Il motivo nella lingua corrente. Un testo sconosciuto (per esempio di una libreria) resta com'è."""
        if not error:
            return ""
        for template in cls.TEMPLATES:
            match = cls._pattern(template).fullmatch(error)
            if match:
                params = match.groupdict()
                if "detail" in params and not template.startswith(cls.PARTIAL_OCR):
                    params["detail"] = cls.display(params["detail"])
                return tr(template, **params)
        return error

    @classmethod
    def kind(cls, error: str | None) -> str:
        """password, limit, pypdf, tesseract, pdftoppm, partial, p7m, too_large oppure other."""
        low = (error or "").lower()
        return next((name for name, word in cls.KINDS if word in low), "other")
