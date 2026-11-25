# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Stemming italiano con Snowball (`snowballstemmer`, BSD-3, solo Python, senza rete):
contratti/contratto -> contratt. La radice è senza accenti, come l'indice FTS5."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_snowball = None  # si carica alla prima parola: chi non cerca né indicizza non paga i ~3 MB di RAM


def _stemmer():
    global _snowball
    if _snowball is None:
        import snowballstemmer
        _snowball = snowballstemmer.stemmer("italian")
    return _snowball


def strip_accents(w: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", w) if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=50_000)
def stem(word: str) -> str:
    return strip_accents(_stemmer().stemWord(word.lower()))


def stem_text(text: str) -> str:
    return " ".join(sorted({stem(w) for w in re.findall(r"\w+", text) if len(w) >= 3}))
