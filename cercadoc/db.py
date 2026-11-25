# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Database SQLite (FTS5): creazione dello schema, azzeramento e diagnostica."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from .config import APP_DIR, DB_PATH
from . import relations, protection


SCHEMA_VERSION = 15  # posizioni strutturate e ricerca nei passaggi


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


def _enable_wal(con):
    # Due processi possono trovare insieme un archivio appena creato. SQLite
    # può rifiutare il cambio di journal immediatamente anche con busy_timeout.
    deadline = time.monotonic() + 30
    while True:
        try:
            if con.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                con.execute("PRAGMA journal_mode=WAL").fetchone()
            return
        except Exception as exc:
            if "database is locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def connect() -> sqlite3.Connection:
    APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    con = protection.connection(DB_PATH)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        _enable_wal(con)
        if con.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            con.execute("BEGIN IMMEDIATE")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                _migrate(con, version)
                con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            con.commit()
        return con
    except BaseException:
        con.close()  # SQLite annulla anche un'eventuale migrazione in corso
        raise


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
        CREATE TABLE IF NOT EXISTS stato(id INTEGER PRIMARY KEY CHECK(id=1), pausa INTEGER NOT NULL DEFAULT 0,
            lavoro TEXT NOT NULL DEFAULT '', avanzamento TEXT NOT NULL DEFAULT '{"phase":"idle"}',
            indicizzati INTEGER NOT NULL DEFAULT 0, riprova_vettori REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sorgenti(path TEXT PRIMARY KEY, mount TEXT NOT NULL, assente_da REAL);
        CREATE TABLE IF NOT EXISTS esclusioni(path TEXT PRIMARY KEY, cartella INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS vettori_da_togliere(id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS origini_cache_ocr(path TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(path,key));
        CREATE TABLE IF NOT EXISTS ricerche(id INTEGER PRIMARY KEY, nome TEXT UNIQUE,
            criteri TEXT NOT NULL, usata INTEGER NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS cronologia_criteri ON ricerche(criteri) WHERE nome IS NULL;
        CREATE TABLE IF NOT EXISTS docs(
            id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            folder TEXT NOT NULL, ext TEXT NOT NULL, size INTEGER, mtime REAL,
            notext INTEGER DEFAULT 0, ocr INTEGER DEFAULT 0, blank_pages INTEGER DEFAULT 0,
            dev INTEGER, ino INTEGER, fp TEXT, error TEXT DEFAULT '');
        INSERT OR IGNORE INTO stato(id,indicizzati) SELECT 1,count(*) FROM docs;
        CREATE TRIGGER IF NOT EXISTS stato_file_aggiunto AFTER INSERT ON docs BEGIN
            UPDATE stato SET indicizzati=indicizzati+1 WHERE id=1;
        END;
        CREATE TRIGGER IF NOT EXISTS stato_file_rimosso AFTER DELETE ON docs BEGIN
            UPDATE stato SET indicizzati=indicizzati-1 WHERE id=1;
        END;
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

    if version < 15:
        con.execute("ALTER TABLE docs ADD COLUMN struttura INTEGER NOT NULL DEFAULT 0")
        con.execute("ALTER TABLE pages ADD COLUMN tipo TEXT NOT NULL DEFAULT 'page'")
        con.execute("ALTER TABLE pages ADD COLUMN titolo TEXT NOT NULL DEFAULT ''")
        con.execute("ALTER TABLE pages ADD COLUMN lettura TEXT NOT NULL DEFAULT ''")
        con.execute("UPDATE analisi SET regole=''")
        con.execute("DELETE FROM vec_done WHERE doc_id IN (SELECT doc_id FROM passaggi GROUP BY doc_id HAVING count(*)>1)")


def worker_status(con):
    run, paused, progress, count = con.execute(
        "SELECT lavoro, pausa, avanzamento, indicizzati FROM stato WHERE id=1").fetchone()
    return dict(json.loads(progress), run=run, manual_pause=bool(paused), indexed=count)


def set_paused(con, paused):
    with con:
        con.execute("UPDATE stato SET pausa=? WHERE id=1", (int(paused),))


def try_set_paused(con, paused):
    timeout = con.execute("PRAGMA busy_timeout").fetchone()[0]
    try:
        con.execute("PRAGMA busy_timeout=0")
        set_paused(con, paused)
    except Exception as exc:
        if "database is locked" in str(exc).lower():
            return False
        raise
    finally:
        con.execute(f"PRAGMA busy_timeout={timeout}")
    return True


def publish_progress(con, run, progress):
    with con:
        con.execute("UPDATE stato SET lavoro=?, avanzamento=? WHERE id=1", (run, json.dumps(progress)))


def reset_index(con: sqlite3.Connection) -> None:
    """Svuota completamente l'indice per una ricostruzione da zero."""
    with con:
        for table in ("docs", "fts", "dirs", "ocr_cache", "vec_chunks", "vec_done", "pages", "sorgenti", "origini_cache_ocr", "vettori_da_togliere"):
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
