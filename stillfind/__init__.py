# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""StillFind - ricerca full-text offline nei tuoi documenti."""
from . import offline

# Prima di ogni altro modulo: da qui in poi la rete non è raggiungibile.
offline._block_network_safe()
