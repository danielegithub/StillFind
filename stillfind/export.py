# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Esportazione dei risultati in CSV."""
from __future__ import annotations

import csv
from datetime import datetime

from .search import HIT_END, HIT_START

COLUMNS = ("Nome", "Cartella", "Percorso", "Tipo", "Modificato", "Dimensione (byte)", "Estratto")


def export_csv(rows: list[dict], path: str) -> int:
    """Scrive i risultati in un CSV (UTF-8 con BOM, separatore `;`: si apre bene con Excel in italiano)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLUMNS)
        for r in rows:
            snippet = (r.get("snippet") or "").replace(HIT_START, "").replace(HIT_END, "").replace("\n", " ")
            w.writerow([r["name"], r["folder"], r["path"], r["ext"].upper(),
                        datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M"), r["size"], snippet])
    return len(rows)
