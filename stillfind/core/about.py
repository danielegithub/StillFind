# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Informazioni su StillFind: nome, autore, licenza (avvisi legali richiesti dalla GPL-3.0, sezione 5d)."""
from __future__ import annotations

from stillfind.core.i18n import tr

__version__ = "0.1.0"
AUTHOR = "Daniele Sisto"
COPYRIGHT = f"Copyright (C) 2026 {AUTHOR}"
LICENSE_NAME = "GNU General Public License versione 3 o successiva (GPL-3.0-or-later)"


def license_name() -> str:
    return tr("GNU General Public License versione 3 o successiva (GPL-3.0-or-later)")


def notice() -> str:
    """Avvisi legali richiesti dalla GPL-3.0 (sezione 5d), nella lingua dell'interfaccia."""
    return (tr("StillFind, di {author}", author=AUTHOR) + f"\n{COPYRIGHT}\n\n"
            + tr("StillFind è software libero: puoi ridistribuirlo e modificarlo secondo i termini della "
                 "{license}, pubblicata dalla Free Software Foundation.", license=license_name()) + "\n\n"
            + tr("Il programma è distribuito nella speranza che sia utile, ma SENZA ALCUNA GARANZIA, "
                 "nemmeno implicita di commerciabilità o di idoneità a uno scopo particolare.") + "\n\n"
            + tr("Chi distribuisce una versione modificata deve conservare questa attribuzione e pubblicare "
                 "il codice sorgente con la stessa licenza.") + "\n\n"
            + tr("La protezione dell’indice usa SQLCipher Community di Zetetic e sqlcipher3. "
                 "L’oscuramento PDF usa PDFium, pypdfium2 e Pillow. Gli avvisi sono in THIRD_PARTY_NOTICES."))


def version_text() -> str:
    return (f"StillFind {__version__}\n{COPYRIGHT}\n" + tr("Licenza: {license}", license=license_name())
            + "\n" + tr("Nessuna garanzia."))
