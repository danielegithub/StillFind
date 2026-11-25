# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Contratto comune degli estrattori di testo."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ExtractionRequest:
    """Ciò che serve a leggere un file: percorso, tipo, impostazioni e canali di ritorno.

    `info`, se presente, riceve dati accessori (`meta`, `pages`, `sections`, `blank_pages`, `document_date`…).
    `cache` conserva le pagine già passate all'OCR."""

    path: str
    ext: str
    cfg: dict
    stop: threading.Event | None = None
    cache: object | None = None
    info: dict | None = None

    def derive(self, path: str, ext: str) -> "ExtractionRequest":
        """La stessa richiesta per un file contenuto in questo (allegato, documento firmato…)."""
        return ExtractionRequest(path, ext, self.cfg, self.stop, None, self.info)


class Extractor(ABC):
    """Legge il testo di uno o più tipi di file. Restituisce (testo, ocr_usato)."""

    extensions: tuple[str, ...] = ()

    @abstractmethod
    def extract(self, request: ExtractionRequest) -> tuple[str, bool]:
        ...
