# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Percorsi, configurazione predefinita e lettura/scrittura di config.json."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


APP_DIR = Path(os.environ.get("CERCADOC_HOME") or Path.home() / ".cercadoc")
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "index.db"

DEFAULT_CONFIG = {
    "folders": [],
    "extensions": ["txt", "md", "csv", "log", "rtf", "html", "htm", "xml", "json",
                   "tex", "docx", "xlsx", "pptx", "odt", "ods", "odp", "pdf", "eml", "p7m", "zip", "epub"],
    "formats_version": 2,
    "exclude_dirs": ["node_modules", "__pycache__", "venv", "Library", "$RECYCLE.BIN"],
    "max_file_mb": 50,
    "max_chars_per_file": 5_000_000,
    "ocr": False,            # richiede Tesseract installato (vedi README)
    "ocr_lang": "ita+eng",
    "ocr_max_pages": 50,
    "auto_scan_seconds": 60,  # controllo automatico di file nuovi/modificati/spostati (0 = mai)
    "network_sandbox": True,   # il lavoratore parte in un recinto senza rete (se il sistema lo permette)
    "pause_on_battery": False,  # a batteria la lettura dei documenti aspetta l'alimentatore
    "semantic_search": False,  # ricerca per significato (serve il modello in `models/`, vedi semantic.py)
    "deadline_alerts": True,
    "global_shortcut": True,
    "search_history": True,
    "entity_types": ["cf", "piva", "iban", "protocollo", "email", "soggetto", "messaggio"],
    "protocol_prefixes": ["PR", "PROT", "PRATICA", "PROTOCOLLO"],
    "semantic_max_passages": 300,  # passaggi (~120 parole) calcolati al massimo per ogni documento
    "semantic_min_score": 0.835,
    "semantic_calibration": 2,
    "cpu_throttle": 0.5,      # pausa in % del tempo di calcolo, per lasciare libero il PC (0 = massima velocità)
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            saved = {}
        if "extensions" in saved and saved.get("formats_version", 1) == 1:
            previous = set(DEFAULT_CONFIG["extensions"]) - {"zip", "epub"}
            if set(saved["extensions"]) == previous:
                saved["extensions"] = [*saved["extensions"], "zip", "epub"]
            saved["formats_version"] = 2
        if saved.get("semantic_calibration", 1) == 1 and saved.get("semantic_min_score") == 0.80:
            saved.pop("semantic_min_score")  # il valore predefinito precedente veniva salvato anche senza modificarlo
        cfg.update(saved)
        cfg["semantic_calibration"] = DEFAULT_CONFIG["semantic_calibration"]
    except (OSError, ValueError):
        pass
    cfg["folders"] = normalize_folders(cfg["folders"])
    return cfg


def normalize_folders(folders) -> list[dict]:
    """Ogni cartella è {"path": ..., "recursive": bool}. Accetta anche semplici stringhe."""
    out = []
    for f in folders:
        if isinstance(f, str):
            f = {"path": f, "recursive": True}
        out.append({"path": str(f["path"]), "recursive": bool(f.get("recursive", True))})
    return out


def save_config(cfg: dict) -> None:
    APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".config-", dir=APP_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, CONFIG_PATH)
    finally:
        Path(name).unlink(missing_ok=True)
