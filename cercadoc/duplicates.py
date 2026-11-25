# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Documenti duplicati: candidati dall'impronta già nel database, confermati con SHA-256 completo."""
from __future__ import annotations

import hashlib
import threading

from .errors import Stopped

CHUNK = 1024 * 1024


def sha256_file(path: str, stop: threading.Event | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            if stop is not None and stop.is_set():
                raise Stopped()
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(con, stop: threading.Event | None = None, progress=None) -> list[dict]:
    """Gruppi di file con contenuto identico: [{"sha256", "size", "paths", "wasted"}], dai più sprecati.

    I candidati hanno stessa dimensione e stessa impronta (`docs.fp`, primi e ultimi 64 KB), ma due file
    diversi possono avere la stessa impronta: per questo ogni candidato si legge per intero e se ne
    calcola lo SHA-256. I file vuoti e quelli non più leggibili sono ignorati."""
    groups = con.execute(
        "SELECT size, fp FROM docs WHERE size > 0 AND fp IS NOT NULL GROUP BY size, fp HAVING COUNT(*) > 1"
    ).fetchall()
    out: list[dict] = []
    for i, (size, fp) in enumerate(groups):
        if progress:
            progress(i, len(groups))
        by_hash: dict[str, list[str]] = {}
        for (path,) in con.execute("SELECT path FROM docs WHERE size = ? AND fp = ? ORDER BY path", (size, fp)):
            try:
                by_hash.setdefault(sha256_file(path, stop), []).append(path)
            except OSError:
                continue
        for digest, paths in by_hash.items():
            if len(paths) > 1:
                out.append({"sha256": digest, "size": size, "paths": paths, "wasted": size * (len(paths) - 1)})
    out.sort(key=lambda g: (-g["wasted"], g["paths"][0]))
    return out
