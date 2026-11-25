# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Eccezioni condivise."""
from __future__ import annotations


class Stopped(Exception):
    """Segnale interno per interrompere subito estrazione/OCR su richiesta."""
