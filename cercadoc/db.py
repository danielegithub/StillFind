# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Database SQLite (FTS5): creazione dello schema, azzeramento e diagnostica."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .config import APP_DIR, DB_PATH
from . import relations, protection


SCHEMA_VERSION = 12  # ricerche personali e stato di lettura dei contenitori


def vector_files(base):
    """Toglie i file dei vettori (`index.db.vec` e `index.db.ids`, vedi `vectors.py`): senza le tabelle
    `vec_*` non avrebbero più significato. Qui niente `numpy`: l'agente importa questo modulo."""
    paths = [Path(str(base) + suffix) for suffix in (".vec", ".ids", ".vec.new", ".ids.new", ".vec.current", ".vec.next")]
    for kind in (".vec.", ".ids."):
        paths.extend(p for p in base.parent.glob(base.name + kind + "*") if re.fullmatch(r"\.[0-9a-f]{32}", p.suffix))
    return [p for p in paths if p.exists()]


def _delete_vector_files() -> None:
    for path in vector_files(DB_PATH):
        try:
            path.unlink()
        except OSError:
            pass


def connect() -> sqlite3.Connection:
    APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    con = protection.connection(DB_PATH)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    if con.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return con
    # Un'altra finestra o il lavoratore possono aprire l'archivio durante il primo avvio.
    con.execute("BEGIN IMMEDIATE")
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            _migrate(con, version)
            con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        con.commit()
    except BaseException:
        con.rollback()
        con.close()
        raise
    return con


def _migrate(con, version):
    if version < 7:
        for table in ("vec_chunks", "vec_done", "pages", "docs", "fts", "dirs"):
            con.execute(f"DROP TABLE IF EXISTS {table}")
        _delete_vector_files()
    elif version == 7:
        # Conserviamo testo e OCR anche per le cartelle al momento scollegate.
        from .stemmer import stem_text
        con.create_function("radici", 1, stem_text)
        con.execute('CREATE VIRTUAL TABLE fts_nuovo USING fts5(name,content,stem,nstem,'
                    'tokenize="unicode61 remove_diacritics 2")')
        con.execute("INSERT INTO fts_nuovo(rowid,name,content,stem,nstem) "
                    "SELECT rowid,name,content,radici(coalesce(content,'')),radici(name) FROM fts")
        con.execute("DROP TABLE fts")
        con.execute("ALTER TABLE fts_nuovo RENAME TO fts")
    schema = """
        CREATE TABLE IF NOT EXISTS ricerche(id INTEGER PRIMARY KEY, nome TEXT UNIQUE,
            criteri TEXT NOT NULL, usata INTEGER NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS cronologia_criteri ON ricerche(criteri) WHERE nome IS NULL;
        CREATE TABLE IF NOT EXISTS docs(
            id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            folder TEXT NOT NULL, ext TEXT NOT NULL, size INTEGER, mtime REAL,
            notext INTEGER DEFAULT 0, ocr INTEGER DEFAULT 0, blank_pages INTEGER DEFAULT 0,
            dev INTEGER, ino INTEGER, fp TEXT, error TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS docs_ino ON docs(dev, ino);
        CREATE TABLE IF NOT EXISTS container_state(doc_id INTEGER PRIMARY KEY REFERENCES docs ON DELETE CASCADE,
            ocr_checked INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS dirs(path TEXT PRIMARY KEY, parent TEXT);
        -- dove comincia ogni pagina dei PDF nel testo del documento (posizione in caratteri): serve a dire
        -- «Pag. 14» per un risultato. La pagina `n` ha inizio `start`; vale fino all'inizio della successiva.
        CREATE TABLE IF NOT EXISTS pages(
            doc_id INTEGER NOT NULL, n INTEGER NOT NULL, start INTEGER NOT NULL, PRIMARY KEY(doc_id, n)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS ocr_cache(key TEXT, page INTEGER, text TEXT, PRIMARY KEY(key, page));
        -- ricerca per significato (semantic.py): passaggi con vettore (il numero è anche quello nel file dei
        -- vettori) e documenti già calcolati, con data e dimensione al momento del calcolo
        CREATE TABLE IF NOT EXISTS vec_chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS vec_chunks_doc ON vec_chunks(doc_id);
        CREATE TABLE IF NOT EXISTS vec_done(doc_id INTEGER PRIMARY KEY, mtime REAL, size INTEGER, model TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
            name, content, stem, nstem, tokenize="unicode61 remove_diacritics 2");
    """ + relations.SCHEMA
    # executescript farebbe un COMMIT prima del DDL; anche il trigger deve restare atomico.
    statement = ""
    for line in schema.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            con.execute(statement)
            statement = ""


def reset_index(con: sqlite3.Connection) -> None:
    """Svuota completamente l'indice per una ricostruzione da zero."""
    with con:
        for table in ("docs", "fts", "dirs", "ocr_cache", "vec_chunks", "vec_done", "pages"):
            con.execute(f"DELETE FROM {table}")
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='vector_values'").fetchone():
            con.execute("DELETE FROM vector_values")
    _delete_vector_files()


def diagnostics(con: sqlite3.Connection) -> dict:
    """Statistiche dell'indice ed elenco di file con errori o PDF senza testo."""
    total, ocr_n, notext_n, err_n = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(ocr),0), COALESCE(SUM(notext),0), "
        "COALESCE(SUM(CASE WHEN error != '' THEN 1 ELSE 0 END),0) FROM docs"
    ).fetchone()
    err_rows = con.execute(
        "SELECT path, name, folder, ext, error FROM docs WHERE error != '' ORDER BY name COLLATE NOCASE"
    ).fetchall()
    notext_pdfs = con.execute(
        "SELECT path, name, folder, size, ocr FROM docs "
        "WHERE ext = 'pdf' AND notext = 1 AND error = '' ORDER BY name COLLATE NOCASE"
    ).fetchall()
    try:
        db_bytes = DB_PATH.stat().st_size
    except OSError:
        db_bytes = 0
    return {
        "total": total, "ocr": ocr_n, "notext": notext_n, "errors": err_n, "db_bytes": db_bytes,
        "error_docs": [dict(zip(("path", "name", "folder", "ext", "error"), r)) for r in err_rows],
        "notext_pdfs": [dict(zip(("path", "name", "folder", "size", "ocr"), r)) for r in notext_pdfs],
    }
