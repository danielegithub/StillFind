# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Ricerche personali e mappa delle menzioni nell'archivio dell'account."""
import json
import re
import time
from pathlib import Path

from stillfind.core.i18n import tr


class SavedSearches:
    """Cronologia delle ricerche e ricerche con nome."""

    def __init__(self, con):
        self.con = con

    def save(self, criteria, name=None):
        con = self.con
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError(tr("Dai un nome alla ricerca."))
        encoded = json.dumps(criteria, ensure_ascii=False, sort_keys=True)
        with con:
            if name is None:
                con.execute("DELETE FROM ricerche WHERE nome IS NULL AND criteri=?", (encoded,))
            con.execute("INSERT INTO ricerche(nome,criteri,usata) VALUES(?,?,?) ON CONFLICT(nome) "
                        "DO UPDATE SET criteri=excluded.criteri,usata=excluded.usata", (name, encoded, time.time_ns()))
            con.execute("DELETE FROM ricerche WHERE id IN (SELECT id FROM ricerche WHERE nome IS NULL "
                        "ORDER BY usata DESC LIMIT -1 OFFSET 60)")

    def listing(self, saved):
        con = self.con
        rows = con.execute("SELECT id,nome,criteri FROM ricerche WHERE (nome IS NOT NULL)=? "
                           "ORDER BY usata DESC,id DESC", (saved,))
        return [(i, name, json.loads(criteria)) for i, name, criteria in rows]


class PersonalDataAudit:
    """Mappa delle menzioni di dati personali nei documenti indicizzati."""

    PERSONAL = ("cf", "piva", "iban", "email", "soggetto")

    def __init__(self, con):
        self.con = con

    def find(self, subject="", folders=(), kind=None):
        """Una riga per file e valore. Se si passa una cartella si includono soltanto i suoi discendenti."""
        con = self.con
        kinds = [kind] if kind in self.PERSONAL else self.PERSONAL
        cond = ["e.tipo IN (" + ",".join("?" for _ in kinds) + ")"]
        args = list(kinds)
        subject = subject.strip()
        if subject:
            forms = sorted({subject, subject.casefold(), subject.upper(), "".join(subject.split()).upper()})
            cond.append("e.valore_normalizzato IN (" + ",".join("?" for _ in forms) + ")")
            args.extend(forms)
        folder_args = []
        folder_sql = ""
        if folders:
            from stillfind.search.query import SearchFilters
            folder_sql = " AND (" + " OR ".join("(d.folder=? OR d.folder LIKE ? ESCAPE '\\')" for _ in folders) + ")"
            for folder in folders:
                folder_args.extend(SearchFilters.under_clause(str(folder)))
        rows = con.execute("""SELECT d.id,d.name,d.path,e.tipo,e.etichetta,min(m.start),count(*),min(m.end) FROM menzioni m
            JOIN entita e ON e.id=m.entita_id JOIN passaggi p ON p.id=m.passaggio_id
            JOIN docs d ON d.id=p.doc_id WHERE """ + " AND ".join(cond) + folder_sql +
            " GROUP BY d.id,e.id ORDER BY d.path,e.tipo,e.etichetta", [*args, *folder_args]).fetchall()
        if not subject or kind or not re.search(r"\w", subject):
            return rows
        # Anche i nomi privi di una regola hanno una posizione verificabile nel testo.
        fts_query = 'content:"' + subject.replace('"', '""') + '"'
        pattern = re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in subject.split()) + r"(?!\w)", re.I)
        known = {r[0] for r in rows}
        for doc, name, path, text in con.execute("SELECT d.id,d.name,d.path,f.content FROM fts f "
                    "JOIN docs d ON d.id=f.rowid WHERE fts MATCH ?" + folder_sql, [fts_query, *folder_args]):
            if doc not in known:
                matches = pattern.finditer(text or "")
                first = next(matches, None)
                if first:
                    rows.append((doc, name, path, "testo", first[0], first.start(), 1 + sum(1 for _ in matches), first.end()))
        return sorted(rows, key=lambda row: (row[2], row[3], row[5]))

    @staticmethod
    def exposure_folders():
        return [str(Path.home() / name) for name in ("Desktop", "Downloads", "Scrivania", "Download")
                if (Path.home() / name).is_dir()]
