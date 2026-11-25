# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Grafo locale, cronologia e scadenze. Le posizioni si riferiscono al testo conservato da FTS."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from stillfind.core import facts
from stillfind.indexing.versions import TextVersions
from stillfind.core.stemmer import stem_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS passaggi(
    id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL REFERENCES docs ON DELETE CASCADE,
    start INTEGER NOT NULL, end INTEGER NOT NULL, pagina INTEGER, UNIQUE(doc_id,start));
CREATE VIRTUAL TABLE IF NOT EXISTS brani_fts USING fts5(content, stem, tokenize="unicode61 remove_diacritics 2");
CREATE TRIGGER IF NOT EXISTS cancella_brano AFTER DELETE ON passaggi BEGIN
    DELETE FROM brani_fts WHERE rowid=old.id;
END;
CREATE TABLE IF NOT EXISTS entita(
    id INTEGER PRIMARY KEY, tipo TEXT NOT NULL, valore_normalizzato TEXT NOT NULL, etichetta TEXT NOT NULL,
    UNIQUE(tipo,valore_normalizzato));
CREATE TABLE IF NOT EXISTS menzioni(
    entita_id INTEGER NOT NULL REFERENCES entita ON DELETE CASCADE,
    passaggio_id INTEGER NOT NULL REFERENCES passaggi ON DELETE CASCADE,
    ruolo TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
    PRIMARY KEY(passaggio_id, entita_id, start)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS menzioni_entita ON menzioni(entita_id,passaggio_id);
CREATE TRIGGER IF NOT EXISTS cancella_entita_orfana AFTER DELETE ON menzioni
WHEN NOT EXISTS (SELECT 1 FROM menzioni WHERE entita_id=old.entita_id)
BEGIN DELETE FROM entita WHERE id=old.entita_id; END;
CREATE TABLE IF NOT EXISTS collegamenti(
    da_doc INTEGER REFERENCES docs ON DELETE CASCADE, a_doc INTEGER REFERENCES docs ON DELETE CASCADE,
    tipo TEXT NOT NULL, peso REAL NOT NULL, PRIMARY KEY(da_doc,a_doc,tipo)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS collegamenti_arrivo ON collegamenti(a_doc,tipo);
CREATE TABLE IF NOT EXISTS importi(
    passaggio_id INTEGER NOT NULL REFERENCES passaggi ON DELETE CASCADE, centesimi INTEGER NOT NULL,
    valuta TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS importi_somma ON importi(valuta,centesimi,passaggio_id);
CREATE INDEX IF NOT EXISTS importi_passaggio ON importi(passaggio_id);
CREATE TABLE IF NOT EXISTS scadenze(
    passaggio_id INTEGER NOT NULL REFERENCES passaggi ON DELETE CASCADE, data_iso TEXT NOT NULL,
    contesto TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS scadenze_data ON scadenze(data_iso);
CREATE INDEX IF NOT EXISTS scadenze_passaggio ON scadenze(passaggio_id);
CREATE TABLE IF NOT EXISTS analisi(
    doc_id INTEGER PRIMARY KEY REFERENCES docs ON DELETE CASCADE, regole TEXT NOT NULL,
    data_iso TEXT, fonte TEXT, sha256 TEXT, contenuto_sha256 TEXT, formato TEXT, inizio_corpo INTEGER DEFAULT 0,
    parole INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS analisi_sha ON analisi(sha256);
CREATE INDEX IF NOT EXISTS analisi_contenuto ON analisi(contenuto_sha256);
CREATE TABLE IF NOT EXISTS firme_testo(
    doc_id INTEGER REFERENCES docs ON DELETE CASCADE, impronta INTEGER NOT NULL, PRIMARY KEY(doc_id,impronta)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS firme_simili ON firme_testo(impronta,doc_id);
CREATE TABLE IF NOT EXISTS regole_grafo(id INTEGER PRIMARY KEY CHECK(id=1), valore TEXT NOT NULL);
"""


class RelationGraph:
    """Grafo locale dei documenti: entità, collegamenti, versioni, cronologia e scadenze."""

    SCHEMA = SCHEMA
    # Nella cronologia il ripiego sulla modifica del file viene sempre segnalato.
    CHRONO_DATE = "coalesce(a.data_iso,date(d.mtime,'unixepoch','localtime'))"
    CHRONO_COLUMNS = f"d.id,d.name,{CHRONO_DATE},coalesce(a.fonte,'modifica file')"

    def __init__(self, con):
        self.con = con

    @staticmethod
    def rules(cfg):
        enabled = sorted(set(cfg.get("entity_types", facts.ENTITY_LABELS)).intersection(facts.ENTITY_LABELS))
        prefixes = sorted({str(x).strip().upper() for x in cfg.get("protocol_prefixes", facts.PROTOCOL_PREFIXES)
                           if str(x).strip()})
        return json.dumps([enabled, prefixes], ensure_ascii=False)

    def set_rules(self, cfg):
        con = self.con
        current = self.rules(cfg)
        row = con.execute("SELECT valore FROM regole_grafo WHERE id=1").fetchone()
        if row and row[0] == current:
            return current
        enabled, prefixes = json.loads(current)
        con.execute("INSERT OR REPLACE INTO regole_grafo VALUES(1,?)", (current,))
        if enabled:
            con.execute(f"DELETE FROM entita WHERE tipo NOT IN ({','.join('?' for _ in enabled)})", enabled)
        else:
            con.execute("DELETE FROM entita")
        if row and json.loads(row[0])[1] != prefixes:
            con.execute("DELETE FROM entita WHERE tipo='protocollo'")
        if "messaggio" not in enabled:
            con.execute("DELETE FROM collegamenti WHERE tipo='risposta'")
        return current

    @staticmethod
    def passage_spans(text, pages):
        """Ogni passaggio appartiene a una sola pagina. Negli altri file si conservano i paragrafi."""
        bounds = [(offset, n) for n, offset in pages if offset < len(text)]
        if not bounds or bounds[0][0] > 0:
            bounds.insert(0, (0, None))
        for ix, (start, page) in enumerate(bounds):
            end = bounds[ix + 1][0] if ix + 1 < len(bounds) else len(text)
            for match in re.finditer(r"\n[ \t\r]*\n|\r[ \t]*\r", text[start:end]):
                # finditer lavora sul segmento originale: il riferimento della pagina resta fisso
                stop = bounds[ix][0] + match.start()
                if stop > start and text[start:stop].strip():
                    yield start, stop, page
                start = bounds[ix][0] + match.end()
            if end > start and text[start:end].strip():
                yield start, end, page

    def _store_mentions(self, pid, base, mentions):
        con = self.con
        for kind, value, label, role, a, b in mentions:
            con.execute("INSERT INTO entita(tipo,valore_normalizzato,etichetta) VALUES(?,?,?) "
                        "ON CONFLICT(tipo,valore_normalizzato) DO NOTHING", (kind, value, label))
            con.execute("INSERT OR IGNORE INTO menzioni SELECT id,?,?,?,? FROM entita WHERE tipo=? AND valore_normalizzato=?",
                        (pid, role, base + a, base + b, kind, value))

    def _direct_links(self, doc_id):
        con = self.con
        # In entrambi gli ordini di arrivo: documento firmato/originale e domanda/risposta.
        for end in ("s", "o"):
            con.execute(f"""INSERT OR IGNORE INTO collegamenti SELECT s.doc_id,o.doc_id,'contiene',1
                FROM analisi s JOIN analisi o ON s.contenuto_sha256=o.sha256
                WHERE s.doc_id<>o.doc_id AND {end}.doc_id=?""", (doc_id,))
        for end in ("p", "q"):
            con.execute(f"""INSERT OR IGNORE INTO collegamenti
                SELECT p.doc_id,q.doc_id,'risposta',1 FROM passaggi p JOIN menzioni m ON m.passaggio_id=p.id
                JOIN menzioni n ON n.entita_id=m.entita_id JOIN passaggi q ON q.id=n.passaggio_id
                WHERE m.ruolo='risposta' AND n.ruolo='id' AND p.doc_id<>q.doc_id AND {end}.doc_id=?""", (doc_id,))

    def index_document(self, doc_id, text, pages, ext, info, policy, body_start=0):
        con = self.con
        enabled, prefixes = json.loads(policy)
        protocols = facts.protocol_pattern(prefixes) if "protocollo" in enabled else None
        con.execute("DELETE FROM passaggi WHERE doc_id=?", (doc_id,))
        con.execute("DELETE FROM firme_testo WHERE doc_id=?", (doc_id,))
        con.execute("DELETE FROM collegamenti WHERE da_doc=? OR a_doc=?", (doc_id, doc_id))
        source_type = info.get("inner_ext", ext)
        con.execute("INSERT OR REPLACE INTO analisi(doc_id,regole,sha256,contenuto_sha256,formato,inizio_corpo) "
                    "VALUES(?,?,?,?,?,?)", (doc_id, policy, info.get("sha256"), info.get("inner_sha256"),
                                           source_type, body_start))
        invoice = source_type == "xml" and "Fattura numero " in text
        invoice_units = set(re.findall(r"importo totale [\d.+-]*[ \t]+([A-Z]{3})\b", text)) if invoice else set()
        mail = source_type == "eml"
        header_end = text.find("\n\n", body_start) if mail else 0
        if header_end == -1:
            header_end = len(text)
        internal = info.get("document_date")
        first = {}
        for start, end, page in self.passage_spans(text, pages):
            pid = con.execute("INSERT INTO passaggi(doc_id,start,end,pagina) VALUES(?,?,?,?)",
                              (doc_id, start, end, page)).lastrowid
            part = text[start:end]
            con.execute("INSERT INTO brani_fts(rowid,content,stem) VALUES(?,?,?)", (pid, part, stem_text(part)))
            self._store_mentions(pid, start, facts.entities(part, enabled, protocols, invoice=invoice,
                                                          mail=mail and body_start <= start < header_end))
            con.executemany("INSERT INTO importi VALUES(?,?,?,?,?)",
                            ((pid, v, unit, start + a, start + b)
                             for v, unit, a, b in facts.amounts(part, invoice_units)))
            for role, iso, a, b in facts.dated_facts(part, start):
                if role == "scadenza":
                    context = " ".join(part[max(0, a-45):min(len(part), b+45)].split())
                    con.execute("INSERT INTO scadenze VALUES(?,?,?,?,?)", (pid, iso, context, start+a, start+b))
                else:
                    first.setdefault(role, iso)
        if internal:
            issued, source = internal
        elif "documento" in first:
            issued, source = first["documento"], "testo"
        elif "intestazione" in first:
            issued, source = first["intestazione"], "intestazione"
        else:
            issued, source = info.get("metadata_date"), "metadati"
        con.execute("UPDATE analisi SET data_iso=?,fonte=? WHERE doc_id=?", (issued, source if issued else None, doc_id))
        TextVersions(con).link(doc_id, text[body_start:])
        self._direct_links(doc_id)

    def document_entities(self, doc_id):
        con = self.con
        return con.execute("""SELECT e.id,e.tipo,e.etichetta,min(m.start),group_concat(DISTINCT nullif(m.ruolo,''))
            FROM passaggi p JOIN menzioni m ON m.passaggio_id=p.id JOIN entita e ON e.id=m.entita_id
            WHERE p.doc_id=? GROUP BY e.id ORDER BY e.tipo,e.etichetta COLLATE NOCASE""", (doc_id,)).fetchall()

    def entity_documents(self, entity_id):
        con = self.con
        return con.execute(f"""SELECT {self.CHRONO_COLUMNS},min(m.start),m.end FROM menzioni m
            JOIN passaggi p ON p.id=m.passaggio_id JOIN docs d ON d.id=p.doc_id LEFT JOIN analisi a ON a.doc_id=d.id
            WHERE m.entita_id=? GROUP BY d.id ORDER BY 3,d.name COLLATE NOCASE,d.id""", (entity_id,)).fetchall()

    def related_documents(self, doc_id):
        con = self.con
        return con.execute(f"""WITH mie AS (
            SELECT DISTINCT m.entita_id FROM passaggi p JOIN menzioni m ON m.passaggio_id=p.id WHERE p.doc_id=?),
            rapporti(other, motivo) AS (
            SELECT q.doc_id,e.etichetta FROM mie JOIN entita e ON e.id=mie.entita_id
            JOIN menzioni n ON n.entita_id=e.id JOIN passaggi q ON q.id=n.passaggio_id
            WHERE q.doc_id<>? GROUP BY q.doc_id,e.id
            UNION ALL SELECT a_doc,CASE tipo WHEN 'contiene' THEN 'documento contenuto' WHEN 'risposta' THEN 'risponde a' ELSE 'versione' END
            FROM collegamenti WHERE da_doc=?
            UNION ALL SELECT da_doc,CASE tipo WHEN 'contiene' THEN 'copia firmata' WHEN 'risposta' THEN 'risposta ricevuta' ELSE 'versione' END
            FROM collegamenti WHERE a_doc=?)
            SELECT {self.CHRONO_COLUMNS},group_concat(DISTINCT motivo) FROM rapporti JOIN docs d ON d.id=other
            LEFT JOIN analisi a ON a.doc_id=d.id GROUP BY d.id ORDER BY 3,d.name COLLATE NOCASE,d.id""",
                           (doc_id, doc_id, doc_id, doc_id)).fetchall()

    def document_versions(self, doc_id):
        con = self.con
        return con.execute("""SELECT d.id,d.name,c.peso FROM collegamenti c
            JOIN docs d ON d.id=CASE WHEN c.da_doc=? THEN c.a_doc ELSE c.da_doc END
            WHERE c.tipo='versione' AND (c.da_doc=? OR c.a_doc=?) ORDER BY c.peso DESC,d.name""",
                           (doc_id, doc_id, doc_id)).fetchall()

    def deadlines(self, start=None, end=None):
        con = self.con
        where, args = [], []
        for op, value in ((">=", start), ("<=", end)):
            if value:
                where.append("s.data_iso " + op + " ?")
                args.append(value)
        return con.execute("""SELECT s.data_iso,d.id,d.name,s.contesto,s.start,s.end FROM scadenze s
            JOIN passaggi p ON p.id=s.passaggio_id JOIN docs d ON d.id=p.doc_id """ +
                           ("WHERE " + " AND ".join(where) if where else "") +
                           " ORDER BY s.data_iso,d.name,s.start", args).fetchall()

    def upcoming_count(self, today=None):
        con = self.con
        today = today or date.today()
        return con.execute("""SELECT count(DISTINCT p.doc_id) FROM scadenze s JOIN passaggi p ON p.id=s.passaggio_id
            WHERE s.data_iso BETWEEN ? AND ?""", (today.isoformat(), (today + timedelta(days=30)).isoformat())).fetchone()[0]
