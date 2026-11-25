# SPDX-License-Identifier: GPL-3.0-or-later
"""Rimozione dal solo archivio e regole per i percorsi da saltare nelle scansioni."""
import os

from . import config


def path_key(path):
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def within(path, directory):
    """Percorsi già normalizzati. Il separatore distingue, ad esempio, Atti da Atti2."""
    return path == directory or path.startswith(os.path.join(directory, ""))


def erase_documents(con, ids):
    """Unità della transazione chiamante. I riferimenti esterni sopravvivono a un crash nella coda di pulizia."""
    con.execute("CREATE TEMP TABLE IF NOT EXISTS da_togliere(id INTEGER PRIMARY KEY)")
    con.execute("DELETE FROM da_togliere")
    con.executemany("INSERT INTO da_togliere VALUES(?)", ((i,) for i in ids))
    con.execute("""DELETE FROM ocr_cache WHERE substr(key,1,instr(key,'|')-1) IN
                   (SELECT fp FROM docs JOIN da_togliere USING(id) WHERE fp IS NOT NULL)""")
    con.execute("INSERT OR IGNORE INTO vettori_da_togliere SELECT id FROM vec_chunks WHERE doc_id IN da_togliere")
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='vector_values'").fetchone():
        con.execute("DELETE FROM vector_values WHERE id IN vettori_da_togliere")
        con.execute("DELETE FROM vettori_da_togliere")
    con.execute("DELETE FROM vec_chunks WHERE doc_id IN da_togliere")
    con.execute("DELETE FROM vec_done WHERE doc_id IN da_togliere")
    con.execute("DELETE FROM pages WHERE doc_id IN da_togliere")
    con.execute("DELETE FROM fts WHERE rowid IN da_togliere")
    # Passaggi, menzioni, collegamenti, scadenze e analisi seguono le chiavi esterne.
    return con.execute("DELETE FROM docs WHERE id IN da_togliere").rowcount


def finish_vector_removal(con):
    if not con.execute("SELECT 1 FROM vettori_da_togliere LIMIT 1").fetchone():
        return
    # Anche se il modello è spento o disinstallato, pulisci i vettori già presenti.
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='vector_values'").fetchone():
        with con:
            con.execute("DELETE FROM vector_values WHERE id IN vettori_da_togliere")
            con.execute("DELETE FROM vettori_da_togliere")
        return
    from .models import DEFAULT_MODEL, MODELS
    from .vector_files import VectorFiles
    store = VectorFiles(config.DB_PATH, MODELS[DEFAULT_MODEL]["dim"])
    ids = {row[0] for row in con.execute("SELECT id FROM vettori_da_togliere")}
    if store.erase(ids):
        with con:
            con.execute("DELETE FROM vettori_da_togliere")


def erase_cached_paths(con, matches):
    keys = {key for path, key in con.execute("SELECT path,key FROM origini_cache_ocr") if matches(path)}
    con.executemany("DELETE FROM ocr_cache WHERE key=?", ((key,) for key in keys))
    con.execute("DELETE FROM origini_cache_ocr WHERE key NOT IN (SELECT key FROM ocr_cache)")


def forget(con, path, folder):
    """Chiamato dal lavoratore sotto worker.lock. Non accede mai al percorso originale."""
    scope = path_key(path)
    def matches(value):
        key = path_key(value)
        return within(key, scope) if folder else key == scope

    with con:
        con.execute("BEGIN IMMEDIATE")
        con.create_function("percorso_da_togliere", 1, matches)
        ids = [r[0] for r in con.execute("SELECT id FROM docs WHERE percorso_da_togliere(path)")]
        dirs = con.execute("DELETE FROM dirs WHERE percorso_da_togliere(path)").rowcount if folder else 0
        if not ids and not dirs:
            exists = con.execute("""SELECT 1 FROM esclusioni WHERE path=? UNION ALL
                                    SELECT 1 FROM origini_cache_ocr WHERE percorso_da_togliere(path) LIMIT 1""",
                                 (scope,)).fetchone()
            if not exists:
                raise ValueError("Il percorso selezionato non è presente nell’archivio.")
        if folder:
            con.execute("DELETE FROM sorgenti WHERE percorso_da_togliere(path)")
        con.execute("INSERT OR REPLACE INTO esclusioni(path,cartella) VALUES(?,?)", (scope, int(folder)))
        removed = erase_documents(con, ids)
        erase_cached_paths(con, matches)
    finish_vector_removal(con)
    return removed
