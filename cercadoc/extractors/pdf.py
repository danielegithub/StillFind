# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Estrazione del testo dei PDF, pagina per pagina, con OCR solo dove manca il testo."""
from __future__ import annotations

import threading

from ..errors import Stopped
from . import ocr


def extract_pdf(path: str, cfg: dict, stop: threading.Event | None = None,
                cache: "ocr._OcrCache | None" = None, info: dict | None = None) -> tuple[str, bool]:
    """Restituisce (testo, ocr_usato). L'OCR parte pagina per pagina solo dove manca testo.
    Se `info` è un dict, vi scrive `blank_pages` (le pagine rimaste senza testo sufficiente) e `pages`: la posizione
    nel testo restituito in cui comincia ogni pagina (la pagina N è `pages[N - 1]`)."""
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
    ocr_used = False
    if cfg.get("ocr") and ocr.ocr_available():
        max_pages = max(1, int(cfg.get("ocr_max_pages", 50) or 50))
        need_ocr = [i + 1 for i, t in enumerate(page_texts[:max_pages]) if cfg.get("_force_ocr") or len(t.strip()) < 40]
        if need_ocr:
            ocr_map = {p: t for p, t in (cache.get() if cache else {}).items() if p in need_ocr}
            rest = [p for p in need_ocr if p not in ocr_map]
            if rest:
                ocr_map.update(ocr.ocr_pdf_pages(path, rest, cfg, stop=stop,
                                             on_page=cache.put if cache else None))
            for p, txt in ocr_map.items():
                if txt.strip():
                    page_texts[p - 1] = txt
            ocr_used = True
    if info is not None:
        info["blank_pages"] = sum(1 for t in page_texts if len(t.strip()) < 40)
        starts, pos = [], 0
        for t in page_texts:  # le pagine sono unite con un a capo: ognuna comincia dopo il testo della precedente
            starts.append(pos)
            pos += len(t) + 1
        info["pages"] = starts
    joined = "\n".join(page_texts)
    page_texts.clear()  # non teniamo in memoria due copie del documento
    return joined, ocr_used
