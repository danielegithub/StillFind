# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Lingue OCR incluse nell'app."""
from __future__ import annotations

import re

from stillfind.core.i18n import mark, tr


class OcrLanguages:
    """Lingue OCR incluse nell'app. Nelle impostazioni la scelta resta una stringa Tesseract («ita+eng»), sempre
    nello stesso ordine: così una stessa scelta dà sempre la stessa chiave nella cache OCR."""

    # (codice Tesseract, nome). Devono corrispondere ai file elencati in packaging/assets.json.
    LANGUAGES: tuple[tuple[str, str], ...] = (
        ("ita", mark("Italiano")),
        ("eng", mark("Inglese")),
        ("fra", mark("Francese")),
        ("spa", mark("Spagnolo")),
        ("deu", mark("Tedesco")),
        ("por", mark("Portoghese")),
    )
    NAMES = dict(LANGUAGES)
    DEFAULT = "ita+eng"

    @classmethod
    def name(cls, code: str) -> str:
        """Il nome della lingua nella lingua dell'interfaccia («Inglese», «English»…)."""
        return tr(cls.NAMES[code]) if code in cls.NAMES else code

    @classmethod
    def parse(cls, value) -> list[str]:
        """I codici riconosciuti in `value` (stringa o elenco), in ordine e senza ripetizioni."""
        if isinstance(value, str):
            value = re.split(r"[+,;\s]+", value.lower())
        wanted = {str(code).strip().lower() for code in value or ()}
        return [code for code, _ in cls.LANGUAGES if code in wanted]

    @classmethod
    def normalize(cls, value) -> str:
        """La stringa da passare a Tesseract; se non resta nessuna lingua valida, quella predefinita."""
        return "+".join(cls.parse(value) or cls.parse(cls.DEFAULT))

    @classmethod
    def describe(cls, value) -> str:
        """«Italiano, Inglese»."""
        return ", ".join(cls.name(code) for code in cls.parse(cls.normalize(value)))
