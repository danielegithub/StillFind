# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Lingua dell'interfaccia, traduzioni e formati di numeri e date.

Nel codice i testi si scrivono in italiano e fanno da chiave: `tr("Impostazioni")`. Le traduzioni stanno in
`stillfind/locales/*.json`, nel formato `{"testo italiano": {"en": "...", "fr": "...", ...}}`; i plurali hanno la
forma `{"one": "...", "other": "..."}`. Un testo senza traduzione resta in italiano.

L'app non deve mai decidere qualcosa confrontando un testo tradotto: per quello ci sono gli identificatori."""
from __future__ import annotations

import functools
import json
import locale
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


class Languages:
    """Le lingue disponibili e la scelta di quella da usare."""

    ALL: tuple[tuple[str, str], ...] = (
        ("it", "Italiano"), ("en", "English"), ("fr", "Français"),
        ("es", "Español"), ("de", "Deutsch"), ("pt", "Português"),
    )
    CODES = tuple(code for code, _ in ALL)
    NATIVE_NAMES = dict(ALL)
    SOURCE = "it"    # lingua dei testi scritti nel codice
    FALLBACK = "en"  # proposta quando la lingua del sistema non è tra quelle disponibili

    @classmethod
    def normalize(cls, code: str | None) -> str:
        """«fr_CA.UTF-8» → «fr»; stringa vuota se la lingua non è disponibile."""
        base = re.split(r"[-_.@]", str(code or "").strip().lower())[0]
        return base if base in cls.CODES else ""

    @classmethod
    @functools.cache
    def system(cls) -> str:
        """La lingua del sistema operativo, oppure l'inglese."""
        candidates: list[str] = []
        try:
            if sys.platform == "darwin":
                out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True,
                                     timeout=3).stdout
                candidates += re.findall(r'"?([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*)"?\s*[,)]', out)
            elif sys.platform.startswith("win"):
                import ctypes
                buffer = ctypes.create_unicode_buffer(85)
                if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, 85):
                    candidates.append(buffer.value)
        except Exception:
            pass
        for variable in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
            candidates += [part for part in os.environ.get(variable, "").split(":") if part]
        try:
            candidates.append(locale.getlocale()[0] or "")
        except Exception:
            pass
        return next((code for code in map(cls.normalize, candidates) if code), cls.FALLBACK)

    @classmethod
    def effective(cls, configured: str | None) -> str:
        """La lingua salvata nelle impostazioni; al primo avvio, quella proposta.
        STILLFIND_LANGUAGE ha la precedenza (utile per provare l'app nelle altre lingue)."""
        return (cls.normalize(os.environ.get("STILLFIND_LANGUAGE")) or cls.normalize(configured)
                or cls.system())


class Translator:
    """Traduce i testi nella lingua corrente. La lingua si sceglie una volta per processo."""

    _language = Languages.SOURCE
    _catalog: dict[str, dict] = {}
    _catalog_language: str | None = None
    _initialised = False

    @classmethod
    def language(cls) -> str:
        return cls._language

    @classmethod
    def set_language(cls, code: str | None) -> str:
        cls._language = Languages.normalize(code) or Languages.SOURCE
        return cls._language

    @classmethod
    def initialise(cls, configured: str | None) -> str:
        """Imposta la lingua al primo caricamento delle impostazioni. Un cambio nelle Impostazioni vale dalla
        prossima apertura, quindi le letture successive del file non devono cambiare i testi già mostrati."""
        if not cls._initialised:
            cls._initialised = True
            cls.set_language(Languages.effective(configured))
        return cls._language

    @staticmethod
    def load_catalogs(directory: Path | None = None) -> dict[str, dict]:
        """Tutte le traduzioni: `{lingua: {testo italiano: traduzione}}`."""
        if directory is None:
            from stillfind.core.resources import locales_dir
            directory = locales_dir()
        catalogs: dict[str, dict] = {code: {} for code in Languages.CODES if code != Languages.SOURCE}
        for path in sorted(directory.glob("*.json")):
            for source, versions in json.loads(path.read_text(encoding="utf-8")).items():
                for code, text in versions.items():
                    if code in catalogs:
                        catalogs[code][source] = text
        return catalogs

    @classmethod
    def reload(cls) -> None:
        cls._catalog, cls._catalog_language = {}, None

    @classmethod
    def _lookup(cls, source: str):
        if cls._language == Languages.SOURCE:
            return None
        if cls._catalog_language != cls._language or not cls._catalog:
            try:
                cls._catalog = cls.load_catalogs()
            except (OSError, ValueError):
                cls._catalog = {}
            cls._catalog_language = cls._language
        return cls._catalog.get(cls._language, {}).get(source)

    @classmethod
    def tr(cls, text: str, /, **params) -> str:
        """Il testo nella lingua corrente, con i segnaposto `{nome}` sostituiti."""
        found = cls._lookup(text)
        out = found if isinstance(found, str) else text
        return out.format(**params) if params else out

    @classmethod
    def trn(cls, one: str, other: str, n: int, /, **params) -> str:
        """Come `tr`, ma sceglie singolare o plurale secondo `n` (disponibile come `{n}`)."""
        found = cls._lookup(one)
        if isinstance(found, dict):
            out = found.get(cls.plural_form(n), found.get("other", other))
        else:
            out = one if n == 1 else other
        return out.format(n=Formats.number(n), **params)

    @classmethod
    def plural_form(cls, n: int, code: str | None = None) -> str:
        """In francese e portoghese anche lo zero è singolare."""
        if (code or cls._language) in ("fr", "pt"):
            return "one" if n in (0, 1) else "other"
        return "one" if n == 1 else "other"

    @staticmethod
    def mark(text: str) -> str:
        """Segna un testo da tradurre più tardi (in tabelle e costanti), con `tr(variabile)`."""
        return text


class Formats:
    """Numeri e date nel formato della lingua corrente."""

    THOUSANDS = {"it": ".", "en": ",", "fr": " ", "es": ".", "de": ".", "pt": "."}
    MONTHS = {
        "it": ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"),
        "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."),
        "es": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"),
        "de": ("Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."),
        "pt": ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"),
    }
    # {d} giorno, {m} mese abbreviato, {y} anno
    SHORT_DATE = {"it": "{d} {m}", "en": "{m} {d}", "fr": "{d} {m}", "es": "{d} {m}", "de": "{d}. {m}", "pt": "{d} {m}"}
    LONG_DATE = {"it": "{d} {m} {y}", "en": "{m} {d}, {y}", "fr": "{d} {m} {y}", "es": "{d} {m} {y}",
                 "de": "{d}. {m} {y}", "pt": "{d} {m} {y}"}
    NUMERIC_DATE = {"it": "{dd}/{mm}/{y}", "en": "{mm}/{dd}/{y}", "fr": "{dd}/{mm}/{y}", "es": "{dd}/{mm}/{y}",
                    "de": "{dd}.{mm}.{y}", "pt": "{dd}/{mm}/{y}"}
    NUMERIC_MONTH = {"it": "{mm}/{y}", "en": "{mm}/{y}", "fr": "{mm}/{y}", "es": "{mm}/{y}", "de": "{mm}.{y}",
                     "pt": "{mm}/{y}"}

    @classmethod
    def number(cls, n: int) -> str:
        return f"{n:,}".replace(",", cls.THOUSANDS[Translator.language()])

    @classmethod
    def date(cls, when: datetime, *, with_year: bool = True) -> str:
        """«12 gen 2024», «Jan 12, 2024»…"""
        language = Translator.language()
        pattern = (cls.LONG_DATE if with_year else cls.SHORT_DATE)[language]
        return pattern.format(d=when.day, m=cls.MONTHS[language][when.month - 1], y=when.year)

    @classmethod
    def numeric_date(cls, year: int, month: int = 0, day: int = 0) -> str:
        """31/12/2025, 12/31/2025 o 31.12.2025 secondo la lingua; anche solo mese e anno, o solo l'anno."""
        language = Translator.language()
        if day:
            return cls.NUMERIC_DATE[language].format(dd=f"{day:02d}", mm=f"{month:02d}", y=year)
        if month:
            return cls.NUMERIC_MONTH[language].format(mm=f"{month:02d}", y=year)
        return str(year)

    @staticmethod
    def month_first() -> bool:
        """In inglese le date numeriche si scrivono mese/giorno/anno."""
        return Translator.language() == "en"


tr = Translator.tr
trn = Translator.trn
mark = Translator.mark
