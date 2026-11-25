# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Estrazione del testo dai file: `extract()` sceglie l'estrattore dall'estensione."""
from __future__ import annotations

import hashlib
import tempfile
import threading
from pathlib import Path

from . import ocr
from .fatturapa import extract_xml
from .mail import extract_eml
from .meta import read_metadata
from .office import extract_docx, extract_odf, extract_pptx, extract_xlsx
from .p7m import guess_ext, unwrap_all
from .pdf import extract_pdf
from .text import extract_html, extract_rtf, extract_text_file


IMAGE_EXTS = {"png", "jpg", "jpeg", "tif", "tiff", "bmp"}


EXTRACTORS = {
    "docx": extract_docx, "pptx": extract_pptx, "xlsx": extract_xlsx,
    "odt": extract_odf, "ods": extract_odf, "odp": extract_odf,
    "html": extract_html, "htm": extract_html, "rtf": extract_rtf,
    "eml": extract_eml, "xml": extract_xml,
}


def _extract_p7m(path: str, cfg: dict, stop, info) -> tuple[str, bool]:
    """Estrae il documento contenuto nel file firmato e ne legge il testo (in una cartella privata e temporanea)."""
    inner = unwrap_all(Path(path).read_bytes())
    if inner is None:
        raise RuntimeError("file .p7m senza documento (firma separata o formato non riconosciuto)")
    max_bytes = int(cfg.get("max_file_mb", 50) or 50) * 1024 * 1024
    if len(inner) > max_bytes:
        raise RuntimeError("documento contenuto oltre il limite di dimensione")
    ext = guess_ext(Path(path).name, inner)
    if info is not None:
        info["inner_sha256"] = hashlib.sha256(inner).hexdigest()
        info["inner_ext"] = ext
    if ext == "p7m" or ext == "bin":
        raise RuntimeError("tipo di documento contenuto non riconosciuto")
    with tempfile.TemporaryDirectory(prefix="cercadoc_p7m_") as tmp:  # permessi solo per l'utente
        f = Path(tmp) / f"contenuto.{ext}"
        f.write_bytes(inner)
        text, used_ocr = extract(str(f), ext, cfg, stop=stop, info=info)
    return text, used_ocr


def extract(path: str, ext: str, cfg: dict, stop: threading.Event | None = None,
            cache: "ocr._OcrCache | None" = None, info: dict | None = None) -> tuple[str, bool]:
    """Restituisce (testo, ocr_usato). `info` riceve `blank_pages` per i PDF e `meta` (metadati) per tutti."""
    if cfg.get("_archive_budget"):
        from .archives import OFFICE
        if ext in OFFICE:
            cfg["_archive_budget"].reserve_office(path)
    if info is not None:
        info["meta"] = read_metadata(path, ext, info)
    if ext == "p7m":
        return _extract_p7m(path, cfg, stop, info)
    if ext in ("zip", "epub"):
        from .archives import extract_archive
        return extract_archive(path, ext, cfg, stop, info)
    if ext == "pdf":
        return extract_pdf(path, cfg, stop=stop, cache=cache, info=info)
    if ext == "xml":
        return extract_xml(path, info), False
    if ext == "eml":
        from .archives import Members
        members = Members(cfg, stop)
        text = extract_eml(path, info, members if not cfg.get("_inside_container") else None)
        members.report(info)
        return text + "".join(members.parts), members.ocr
    if ext in IMAGE_EXTS:
        if not ocr.ocr_available():
            raise RuntimeError("Tesseract non installato")
        return ocr._tesseract(path, cfg["ocr_lang"], stop=stop), True
    return EXTRACTORS.get(ext, extract_text_file)(path), False
