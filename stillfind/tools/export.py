# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Esportazione dei risultati in CSV."""
from __future__ import annotations

import csv
from datetime import datetime

from stillfind.core.i18n import tr
from stillfind.search.engine import HIT_END, HIT_START


class CsvExporter:
    """Salva i risultati di una ricerca in un file CSV."""

    @staticmethod
    def columns() -> tuple[str, ...]:
        return (tr("Nome"), tr("Cartella"), tr("Percorso"), tr("Tipo"), tr("Modificato"), tr("Dimensione (byte)"),
                tr("Estratto"))

    def __init__(self, path: str):
        self.path = path

    def write(self, rows: list[dict]) -> int:
        """Scrive i risultati (UTF-8 con BOM, separatore `;`: si apre bene con Excel in italiano)."""
        with open(self.path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(self.columns())
            for r in rows:
                snippet = (r.get("snippet") or "").replace(HIT_START, "").replace(HIT_END, "").replace("\n", " ")
                w.writerow([r["name"], r["folder"], r["path"], r["ext"].upper(),
                            datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M"), r["size"], snippet])
        return len(rows)
