# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Testi e scelte dell'interfaccia in italiano semplice (senza Tk, quindi si prova con i test)."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta

MONTHS = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")

# categorie comprensibili al posto delle estensioni (DOCX, ODT, P7M…)
CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Documenti di testo", ("txt", "md", "rtf", "odt", "docx", "tex", "log")),
    ("PDF", ("pdf",)),
    ("Fogli di calcolo", ("xlsx", "ods", "csv")),
    ("Presentazioni", ("pptx", "odp")),
    ("Email", ("eml",)),
    ("Archivi e libri", ("zip", "epub")),
    ("Pagine web e dati", ("html", "htm", "xml", "json")),
    ("File firmati", ("p7m",)),
    ("Immagini e foto", ("png", "jpg", "jpeg", "tif", "tiff", "bmp")),
)
ALL_TYPES = "Tutti i tipi"
ALL_FOLDERS = "Tutte le cartelle"
ANY_DATE = "Qualsiasi data"
CUSTOM_DATE = "Scegli le date…"
DATE_CHOICES = (ANY_DATE, "Ultima settimana", "Ultimo mese", "Quest'anno")
# «Cerca in»: il testo del menu e il valore per `search.search(where=...)`
WHERE_CHOICES = (("Nome e contenuto", "all"), ("Solo nel contenuto", "content"), ("Solo nel nome", "name"))
_DAY = 86400.0
_ICONS = {"pdf": "📕", "xlsx": "📊", "ods": "📊", "csv": "📊", "pptx": "📽", "odp": "📽", "eml": "✉️",
          "p7m": "🔏", "png": "🖼", "jpg": "🖼", "jpeg": "🖼", "tif": "🖼", "tiff": "🖼", "bmp": "🖼"}


def category_exts(label: str) -> tuple[str, ...]:
    return next((exts for name, exts in CATEGORIES if name == label), ())


def present_categories(exts) -> list[str]:
    """Le categorie che hanno almeno un documento (per il menu «Tipo»)."""
    have = set(exts)
    return [name for name, group in CATEGORIES if have & set(group)]


def icon(ext: str) -> str:
    return _ICONS.get(ext.lower(), "📄")


def relative_date(ts: float, now: float | None = None) -> str:
    """«oggi», «ieri», «3 giorni fa», «12 gen», «12 gen 2024»."""
    now = time.time() if now is None else now
    d, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    days = (n.date() - d.date()).days
    if days <= 0:
        return "oggi"
    if days == 1:
        return "ieri"
    if days < 7:
        return f"{days} giorni fa"
    label = f"{d.day} {MONTHS[d.month - 1]}"
    return label if d.year == n.year else f"{label} {d.year}"


def date_after(choice: str, now: float | None = None) -> float | None:
    """Inizio dell'intervallo scelto nel menu «Data»; None per «Qualsiasi data»."""
    now = time.time() if now is None else now
    if choice == "Ultima settimana":
        return now - 7 * _DAY
    if choice == "Ultimo mese":
        return now - 30 * _DAY
    if choice == "Quest'anno":
        return datetime(datetime.fromtimestamp(now).year, 1, 1).timestamp()
    return None


_DATE_SPLIT = re.compile(r"[/.\-]")


def parse_period(text: str) -> tuple[float, float, str]:
    """Una data scritta a mano → (inizio, fine esclusa, testo normalizzato). Si capisce «31/12/2025»,
    «31.12.2025», «31-12-2025», «2025-12-31», ma anche solo il mese («12/2025», «2025-12») o l'anno («2025»):
    in quel caso il periodo è tutto il mese o tutto l'anno. ValueError con un messaggio comprensibile se non è una data."""
    t = text.strip()
    bad = ValueError(f"Non capisco la data «{t}». Scrivila così: 31/12/2025 (oppure 12/2025, oppure 2025).")
    parts = _DATE_SPLIT.split(t)
    if not t or not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise bad
    nums = [int(p) for p in parts]
    if len(parts[0]) == 4:      # 2025, 2025-12, 2025-12-31
        y, m, d = nums[0], (nums[1] if len(nums) > 1 else 0), (nums[2] if len(nums) > 2 else 0)
    elif len(parts[-1]) == 4:   # 12/2025, 31/12/2025
        y = nums[-1]
        m, d = (nums[-2] if len(nums) > 1 else 0), (nums[0] if len(nums) == 3 else 0)
    else:
        raise bad
    try:
        if d:
            start = datetime(y, m, d)
            end, label = start + timedelta(days=1), f"{d:02d}/{m:02d}/{y}"
        elif m:
            start = datetime(y, m, 1)
            end, label = datetime(y + (m == 12), m % 12 + 1, 1), f"{m:02d}/{y}"
        else:
            start, end, label = datetime(y, 1, 1), datetime(y + 1, 1, 1), str(y)
    except ValueError:
        raise bad from None
    return start.timestamp(), end.timestamp(), label


def parse_date_range(start_text: str, end_text: str) -> tuple[float | None, float | None, str]:
    """Le due date del dialogo «Scegli le date» (anche una sola) → (dopo, prima, etichetta per il menu).
    «Prima» è escluso e comprende tutto il periodo scritto: «al 12/2025» include tutto dicembre."""
    after = before = None
    first = last = ""
    if start_text.strip():
        after, _, first = parse_period(start_text)
    if end_text.strip():
        _, before, last = parse_period(end_text)
    if after is None and before is None:
        raise ValueError("Scrivi almeno una data.")
    if after is not None and before is not None and after >= before:
        raise ValueError("La data finale è prima di quella iniziale.")
    label = f"{first} – {last}" if first and last else f"dal {first}" if first else f"fino al {last}"
    return after, before, label


def friendly_folder(folder: str, roots: list[str], sep: str = os.sep) -> str:
    """Cartella breve e leggibile: «Documenti › Contratti» invece del percorso completo."""
    folder = folder.rstrip("/\\")
    best = max((r.rstrip("/\\") for r in roots if folder == r.rstrip("/\\") or folder.startswith(r.rstrip("/\\") + sep)),
               key=len, default=None)
    if best:
        parts = [best.rsplit(sep, 1)[-1] or best] + [p for p in folder[len(best):].split(sep) if p]
    else:
        parts = [p for p in folder.split(sep) if p][-2:] or [folder]
    if len(parts) > 3:
        parts = [parts[0], "…"] + parts[-2:]
    return " › ".join(parts)


def thousands(n: int) -> str:
    return f"{n:,}".replace(",", ".")
