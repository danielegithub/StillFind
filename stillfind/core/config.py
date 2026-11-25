# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Percorsi, configurazione predefinita e lettura/scrittura di config.json."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _app_dir():
    if directory := os.environ.get("STILLFIND_HOME") or os.environ.get("CERCADOC_HOME"):
        return Path(directory)
    home = Path.home()
    current = home / ".stillfind"
    previous = home / ".cercadoc"
    # Riusa l'archivio in sede, anche se una vecchia istanza lo sta ancora usando.
    # Se esistono entrambi, un archivio o una configurazione StillFind ha precedenza.
    if previous.is_dir() and not ((current / "index.db").exists() or (current / "config.json").exists()):
        return previous
    return current


APP_DIR = _app_dir()
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
    "offline_retention_days": 0,  # 0 = conserva i documenti delle sorgenti scollegate
    "network_sandbox": True,   # il lavoratore parte in un recinto senza rete (se il sistema lo permette)
    "pause_on_battery": False,  # a batteria la lettura dei documenti aspetta l'alimentatore
    "semantic_search": False,  # ricerca per significato (serve il modello in `models/`, vedi stillfind/semantic)
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


class Settings(dict):
    """Impostazioni dell'utente (`config.json`). Si comporta come un dizionario, con lettura e scrittura su disco."""

    DEFAULTS = DEFAULT_CONFIG

    @classmethod
    def load(cls) -> "Settings":
        cfg = cls(cls.DEFAULTS)
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                saved = {}
            if "extensions" in saved and saved.get("formats_version", 1) == 1:
                previous = set(cls.DEFAULTS["extensions"]) - {"zip", "epub"}
                if set(saved["extensions"]) == previous:
                    saved["extensions"] = [*saved["extensions"], "zip", "epub"]
                saved["formats_version"] = 2
            if saved.get("semantic_calibration", 1) == 1 and saved.get("semantic_min_score") == 0.80:
                saved.pop("semantic_min_score")  # il valore predefinito precedente veniva salvato anche senza modificarlo
            cfg.update(saved)
            cfg["semantic_calibration"] = cls.DEFAULTS["semantic_calibration"]
        except (OSError, ValueError):
            pass
        cfg["folders"] = cls.normalize_folders(cfg["folders"])
        return cfg

    @staticmethod
    def normalize_folders(folders) -> list[dict]:
        """Ogni cartella è {"path": ..., "recursive": bool}. Accetta anche semplici stringhe."""
        out = []
        for f in folders:
            if isinstance(f, str):
                f = {"path": f, "recursive": True}
            out.append({"path": str(f["path"]), "recursive": bool(f.get("recursive", True))})
        return out

    def save(self) -> None:
        APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".config-", dir=APP_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, CONFIG_PATH)
        finally:
            Path(name).unlink(missing_ok=True)

    def add_folder(self, path: str, recursive: bool = True) -> None:
        self["folders"] = [x for x in self["folders"] if x["path"] != path]
        self["folders"].append({"path": path, "recursive": recursive})

    def remove_folder(self, path: str) -> bool:
        before = len(self["folders"])
        self["folders"] = [x for x in self["folders"] if x["path"] != path]
        return len(self["folders"]) != before
