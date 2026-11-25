# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Informazioni su StillFind: nome, autore, licenza (avvisi legali richiesti dalla GPL-3.0, sezione 5d)."""
from __future__ import annotations

__version__ = "0.1.0"
AUTHOR = "Daniele Sisto"
COPYRIGHT = f"Copyright (C) 2026 {AUTHOR}"
LICENSE_NAME = "GNU General Public License versione 3 o successiva (GPL-3.0-or-later)"

NOTICE = (
    f"StillFind, di {AUTHOR}\n"
    f"{COPYRIGHT}\n\n"
    "StillFind è software libero: puoi ridistribuirlo e modificarlo secondo i termini della "
    f"{LICENSE_NAME}, pubblicata dalla Free Software Foundation.\n\n"
    "Il programma è distribuito nella speranza che sia utile, ma SENZA ALCUNA GARANZIA, "
    "nemmeno implicita di commerciabilità o di idoneità a uno scopo particolare.\n\n"
    "Chi distribuisce una versione modificata deve conservare questa attribuzione e pubblicare "
    "il codice sorgente con la stessa licenza.\n\n"
    "La protezione dell’indice usa SQLCipher Community di Zetetic e sqlcipher3. "
    "L’oscuramento PDF usa PDFium, pypdfium2 e Pillow. Gli avvisi sono in THIRD_PARTY_NOTICES."
)


def version_text() -> str:
    return f"StillFind {__version__}\n{COPYRIGHT}\nLicenza: {LICENSE_NAME}\nNessuna garanzia."
