# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Risorse distribuite con l'app, indipendenti dalla cartella corrente."""
from __future__ import annotations

from pathlib import Path
import sys


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def guide_assets_dir() -> Path:
    """Cartella locale delle illustrazioni della guida, sia nel pacchetto compilato sia da sorgente."""
    root = resource_root()
    bundled = root / "guide_assets"
    if getattr(sys, "frozen", False) or bundled.is_dir():
        return bundled
    return root / "stillfind" / "ui" / "guide_assets"


def locales_dir() -> Path:
    """Cartella dei cataloghi di traduzione, sia nel pacchetto compilato sia da sorgente."""
    root = resource_root()
    bundled = root / "locales"
    if getattr(sys, "frozen", False) or bundled.is_dir():
        return bundled
    return root / "stillfind" / "locales"


def brand_dir() -> Path:
    """Cartella dei loghi (marchio e scritta), sia nel pacchetto compilato sia da sorgente."""
    root = resource_root()
    bundled = root / "brand"
    if getattr(sys, "frozen", False) or bundled.is_dir():
        return bundled
    return root / "stillfind" / "ui" / "brand"
