# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Estrazione del testo dai file: il registro sceglie l'estrattore dall'estensione."""
from __future__ import annotations

import threading

from stillfind.extractors.archives import ArchiveExtractor, Members
from stillfind.extractors.base import ExtractionRequest, Extractor
from stillfind.extractors.fatturapa import FatturaPaExtractor
from stillfind.extractors.mail import EmlExtractor
from stillfind.extractors.meta import MetadataReader
from stillfind.extractors.ocr import ImageExtractor
from stillfind.extractors.office import DocxExtractor, OdfExtractor, PptxExtractor, XlsxExtractor
from stillfind.extractors.p7m import P7mExtractor
from stillfind.extractors.pdf import PdfExtractor
from stillfind.extractors.text import HtmlExtractor, PlainTextExtractor, RtfExtractor


class ExtractorRegistry:
    """Associa le estensioni agli estrattori e legge i file. I tipi sconosciuti si leggono come testo semplice."""

    def __init__(self, fallback: Extractor):
        self.fallback = fallback
        self._by_ext: dict[str, Extractor] = {}

    def register(self, extractor: Extractor) -> None:
        for ext in extractor.extensions:
            self._by_ext[ext] = extractor

    def for_extension(self, ext: str) -> Extractor:
        return self._by_ext.get(ext, self.fallback)

    @property
    def image_extensions(self) -> set[str]:
        return set(ImageExtractor.extensions)

    def extract(self, path: str, ext: str, cfg: dict, stop: threading.Event | None = None,
                cache=None, info: dict | None = None) -> tuple[str, bool]:
        """Restituisce (testo, ocr_usato). `info` riceve `blank_pages` per i PDF e `meta` (metadati) per tutti."""
        if cfg.get("_archive_budget") and ext in Members.OFFICE:
            cfg["_archive_budget"].reserve_office(path)
        if info is not None:
            info["meta"] = MetadataReader.read(path, ext, info)
        return self.for_extension(ext).extract(ExtractionRequest(path, ext, cfg, stop, cache, info))


registry = ExtractorRegistry(PlainTextExtractor())
for _extractor in (DocxExtractor(), PptxExtractor(), XlsxExtractor(), OdfExtractor(), HtmlExtractor(),
                   RtfExtractor(), EmlExtractor(), FatturaPaExtractor(), PdfExtractor(), P7mExtractor(),
                   ArchiveExtractor(), ImageExtractor()):
    registry.register(_extractor)
del _extractor
