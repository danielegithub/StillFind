# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Estrazione del testo dei PDF, pagina per pagina, con OCR solo dove manca il testo."""
from __future__ import annotations

from stillfind.core.errors import Stopped
from stillfind.extractors.base import ExtractionRequest, Extractor
from stillfind.extractors.ocr import Tesseract


class PdfExtractor(Extractor):
    extensions = ("pdf",)

    def extract(self, request: ExtractionRequest) -> tuple[str, bool]:
        """Restituisce (testo, ocr_usato). L'OCR parte pagina per pagina solo dove manca testo.
        Se `info` è un dict, vi scrive `blank_pages` (le pagine rimaste senza testo sufficiente) e `pages`: la posizione
        nel testo restituito in cui comincia ogni pagina (la pagina N è `pages[N - 1]`)."""
        path, cfg, stop, cache, info = request.path, request.cfg, request.stop, request.cache, request.info
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("per i PDF installa pypdf:  pip install pypdf")
        import logging
        logging.getLogger("pypdf").setLevel(logging.ERROR)  # niente avvisi su font/struttura: inutili qui
        reader = PdfReader(path)
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                raise RuntimeError("PDF protetto da password")
        page_texts: list[str] = []
        max_chars = int(cfg.get("max_chars_per_file", 0) or 0)
        total = 0
        for page in reader.pages:
            if stop is not None and stop.is_set():
                raise Stopped()
            text = page.extract_text() or ""
            page_texts.append(text)
            total += len(text) + 1
            if max_chars and total > max_chars:
                break  # il resto non verrebbe indicizzato: inutile leggerlo e tenerlo in memoria
        states = ["native" if len(t.strip()) >= 40 else "short" if t.strip() else "pending" for t in page_texts]
        ocr_used = False
        if cfg.get("ocr") and Tesseract.available():
            max_pages = max(1, int(cfg.get("ocr_max_pages", 50) or 50))
            need_ocr = [i + 1 for i, t in enumerate(page_texts[:max_pages]) if cfg.get("_force_ocr") or len(t.strip()) < 40]
            previous = (info or {}).get("previous_ocr", {})
            recognized = dict(previous)
            cached = cache.get() if cache else {}
            recognized.update((p, cached[p]) for p in need_ocr if p in cached)
            rest = [p for p in need_ocr if p not in recognized]
            failures = {}
            if rest:
                if cfg.get("_ocr_started"):
                    cfg["_ocr_started"]()
                try:
                    recognized.update(Tesseract(cfg["ocr_lang"]).recognize_pdf_pages(
                        path, rest, stop=stop, on_page=cache.put if cache else None,
                        on_error=failures.__setitem__ if info is not None else None))
                except (OSError, RuntimeError, ValueError) as exc:
                    if info is None:
                        raise
                    # Il renderer può fallire già in apertura. Il testo digitale rimane disponibile.
                    failures.update(dict.fromkeys(rest, exc))
            for p, txt in recognized.items():
                if p <= len(page_texts):
                    states[p-1] = ("ocr" if txt.strip() else
                                   "native_ocr_empty" if page_texts[p-1].strip() else "ocr_empty")
                    if txt.strip():
                        page_texts[p-1] = txt
            for p in failures:
                states[p-1] = "error"
            if failures:
                p = next(iter(failures))
                info["partial_error"] = f"Lettura parziale · OCR, pag. {p}: {failures[p]}"
            ocr_used = bool(recognized or need_ocr)
        if info is not None:
            info["blank_pages"] = sum(1 for t in page_texts if len(t.strip()) < 40)
            starts, pos = [], 0
            for t in page_texts:  # le pagine sono unite con un a capo: ognuna comincia dopo il testo della precedente
                starts.append(pos)
                pos += len(t) + 1
            info["pages"] = starts
            info["sections"] = [(s, "page", str(n), states[n-1]) for n, s in enumerate(starts, 1)]
        joined = "\n".join(page_texts)
        page_texts.clear()  # non teniamo in memoria due copie del documento
        return joined, ocr_used
