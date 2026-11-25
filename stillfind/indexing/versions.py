# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Impronte bottom-k di gruppi di tre parole e confronto del testo estratto."""
from __future__ import annotations

import heapq
import re
import zlib
from difflib import SequenceMatcher

from stillfind.core.stemmer import strip_accents

class TextVersions:
    """Riconosce le versioni dello stesso testo (impronte bottom-k di terne di parole) e ne mostra le differenze."""

    SKETCH_SIZE = 128
    MIN_WORDS = 6
    MIN_SIMILARITY = .56
    _WORD = re.compile(r"[^\W_]+")
    _TOKENS = re.compile(r"\w+|[^\w\s]")

    def __init__(self, con=None):
        self.con = con

    @staticmethod
    def sketch(text):
        """I 128 hash più piccoli tra le terne distinte. Memoria costante anche per i PDF lunghi."""
        heap, kept = [], set()
        last = []
        count = 0
        for match in TextVersions._WORD.finditer(text):
            last.append(strip_accents(match[0].casefold()))
            count += 1
            if len(last) < 3:
                continue
            value = zlib.crc32(" ".join(last).encode())
            del last[0]
            if value in kept or len(heap) == TextVersions.SKETCH_SIZE and value >= -heap[0]:
                continue
            if len(heap) == TextVersions.SKETCH_SIZE:
                kept.remove(-heapq.heappop(heap))
            heapq.heappush(heap, -value)
            kept.add(value)
        return count, kept if count >= TextVersions.MIN_WORDS else set()

    def link(self, doc_id, text):
        con = self.con
        count, mine = self.sketch(text)
        if not mine:
            return
        con.executemany("INSERT INTO firme_testo(doc_id, impronta) VALUES(?,?)", ((doc_id, n) for n in mine))
        marks = ",".join("?" for _ in mine)
        candidates = con.execute(
            f"SELECT f.doc_id,a.parole FROM firme_testo f JOIN analisi a ON a.doc_id=f.doc_id "
            f"WHERE f.impronta IN ({marks}) AND f.doc_id<>? AND a.parole BETWEEN ? AND ? "
            "GROUP BY f.doc_id HAVING count(*)>=2", [*mine, doc_id, count * .55, count / .55])
        con.execute("UPDATE analisi SET parole=? WHERE doc_id=?", (count, doc_id))
        for other, other_count in candidates:
            theirs = {r[0] for r in con.execute("SELECT impronta FROM firme_testo WHERE doc_id=?", (other,))}
            union = sorted(mine | theirs)[:self.SKETCH_SIZE]
            similarity = sum(v in mine and v in theirs for v in union) / len(union)
            if max(count, other_count) <= 32 and .18 <= similarity < self.MIN_SIMILARITY:
                # In una clausola breve un solo importo cambiato modifica tre terne adiacenti.
                row = con.execute("SELECT substr(f.content,a.inizio_corpo+1) FROM fts f "
                                  "JOIN analisi a ON a.doc_id=f.rowid WHERE a.doc_id=?", (other,)).fetchone()
                first, second = sorted(TextVersions._WORD.findall(strip_accents(s.casefold())) for s in (text, row[0]))
                ratio = SequenceMatcher(None, first, second).ratio()
                if ratio >= .8:
                    similarity = ratio
            if similarity >= self.MIN_SIMILARITY:
                a, b = sorted((doc_id, other))
                con.execute("INSERT OR REPLACE INTO collegamenti VALUES(?,?,'versione',?)", (a, b, similarity))

    @staticmethod
    def differences(left, right):
        """Blocchi affiancati cambiati, con le singole parole diverse. Gli a capo da OCR sono spazi."""
        def passages(text):
            # I confini di frase si conservano anche se il PDF manda a capo a metà riga.
            parts = []
            for sentence in re.split(r"(?<=[.!?])\s+|\n[ \t\r]*\n", text):
                words = sentence.split()
                parts.extend(" ".join(words[i:i + 120]) for i in range(0, len(words), 120))
            return parts

        a, b = passages(left), passages(right)
        for tag, i, j, k, l in SequenceMatcher(None, a, b).get_opcodes():
            if tag == "equal":
                continue
            # I blocchi di paragrafi aggiunti o rimossi non generano righe vuote intermedie.
            old, new = "\n".join(a[i:j]), "\n".join(b[k:l])
            x, y = list(TextVersions._TOKENS.finditer(old)), list(TextVersions._TOKENS.finditer(new))
            matcher = SequenceMatcher(None, [m[0] for m in x], [m[0] for m in y])
            changes = [(p, q, r, s) for op, p, q, r, s in matcher.get_opcodes() if op != "equal"]
            if changes:
                yield (old, [(x[p].start(), x[q-1].end()) for p, q, _, _ in changes if p < q]), (
                    new, [(y[r].start(), y[s-1].end()) for _, _, r, s in changes if r < s])
