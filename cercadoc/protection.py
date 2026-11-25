# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Apertura centralizzata e migrazione atomica dell'archivio locale."""
from __future__ import annotations

import hashlib
import ctypes
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import weakref
from pathlib import Path

from .credentials import credential

CLEAR = b"SQLite format 3\0"
_session = bytearray()
_session_id = None
_mutex = threading.RLock()


class Locked(RuntimeError):
    pass


def forget():
    global _session_id
    with _mutex:
        _session[:] = b"\0" * len(_session)
        _session.clear()
        _session_id = None


def cipher():
    try:
        import sqlcipher3
    except ImportError as exc:
        raise RuntimeError("Il componente locale sqlcipher3 non è installato.") from exc
    return sqlcipher3


def _descriptor(path, salt):
    return Path(str(path) + ".key-" + salt.hex() + ".json")


class _ArchiveSettings:
    def __init__(self, path):
        self.spec = _read_settings(path)


_open_settings = weakref.WeakValueDictionary()


def _archive_settings(path):
    # Su POSIX chiudere un secondo fd sul DB libera i lock SQLite dell'intero
    # processo. Leggiamo la testata prima della prima connessione e riusiamo la
    # modalità finché almeno una connessione tiene aperto lo stesso archivio.
    path = Path(path).resolve()
    with _mutex:
        active = _open_settings.get(path)
        if active is None:
            active = _ArchiveSettings(path)
            _open_settings[path] = active
        return active


def settings(path):
    return _archive_settings(path).spec.copy()


def _read_settings(path):
    try:
        with Path(path).open("rb") as f:
            prefix = f.read(16)
    except FileNotFoundError:
        prefix = b""
    if prefix in (b"", CLEAR):
        return {"mode": "plain"}
    try:
        data = json.loads(_descriptor(path, prefix).read_text("utf-8"))
        if data["mode"] in ("auto", "vault") and re.fullmatch("[0-9a-f]{32}", data["id"]):
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise Locked("Mancano le impostazioni di apertura del database cifrato.")


def remember(spec, key):
    global _session_id
    with _mutex:
        forget()
        _session.extend(key)
        _session_id = spec["id"]


def session_key(spec):
    with _mutex:
        if _session_id != spec["id"] or not _session:
            raise Locked("Cassaforte bloccata. Apri CercaDoc e inserisci la passphrase.")
        return _session.copy()


def _key(path, spec):
    if spec["mode"] == "vault":
        return session_key(spec)
    if spec["mode"] == "auto":
        key = credential("get", spec["id"], path.parent)
        if not key or not re.fullmatch(rb"x'[0-9a-f]{64}'", key):
            raise Locked("La chiave dell’archivio non è disponibile nel portachiavi.")
        return key
    return bytearray()


class Lease:
    """Una connessione trattiene un lock condiviso; la sostituzione del file è esclusiva."""
    def __init__(self, path, exclusive=False):
        self.file = open(str(path) + ".access", "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                from ctypes import wintypes
                self.overlap = ctypes.create_string_buffer(32)
                handle = wintypes.HANDLE(msvcrt.get_osfhandle(self.file.fileno()))
                if not ctypes.windll.kernel32.LockFileEx(handle, 1 | (2 if exclusive else 0), 0,
                                                          1, 0, ctypes.byref(self.overlap)):
                    raise OSError("archivio occupato")
            else:
                import fcntl
                flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(self.file.fileno(), flag | fcntl.LOCK_NB)
        except OSError as exc:
            self.close()
            raise RuntimeError("Archivio occupato. Attendi la fine delle altre operazioni e riprova.") from exc

    def close(self):
        self.file.close()
        self.archive_settings = None


def _open(path, driver, key, lease=None, readonly=False):
    class Connection(driver.Connection):
        def close(self):
            try:
                super().close()
            finally:
                if self._lease:
                    self._lease.close()
                    self._lease = None
    name = str(path)
    if readonly:
        quoted = Path(path).absolute().as_posix().replace("%", "%25").replace("?", "%3F").replace("#", "%23")
        name = "file:" + quoted + "?mode=ro"
    con = driver.connect(name, timeout=30, factory=Connection, uri=readonly)
    con._lease = lease
    con.encrypted = bool(key)
    try:
        if key:
            con.execute("PRAGMA cipher_memory_security=ON")
            con.set_key(key)
            if not con.execute("PRAGMA cipher_version").fetchone():
                raise RuntimeError("SQLCipher non disponibile.")
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return con
    except BaseException:
        con.close()
        raise


def connection(path, readonly=False):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    recover(path)
    lease = Lease(path)
    key = bytearray()
    try:
        lease.archive_settings = _archive_settings(path)
        spec = lease.archive_settings.spec
        key = _key(path, spec)
        return _open(path, cipher() if key else sqlite3, key, lease, readonly)
    except BaseException:
        lease.close()
        raise
    finally:
        key[:] = b"\0" * len(key)


def unlock(path, phrase):
    spec = settings(path)
    if spec["mode"] != "vault":
        raise RuntimeError("L'archivio non usa una passphrase.")
    key = _phrase_key(phrase)
    try:
        con = _open(path, cipher(), key)
        try:
            _remember_derived(con, spec, key)
        finally:
            con.close()
    except cipher().DatabaseError as exc:
        raise Locked("Passphrase errata o archivio illeggibile.") from exc
    finally:
        key[:] = b"\0" * len(key)


def _phrase_key(phrase):
    # Il prefisso distingue sempre una passphrase dalla sintassi per chiavi binarie di SQLCipher.
    return bytearray(b"CercaDoc:") + (phrase.encode("utf-8") if isinstance(phrase, str) else phrase)


def _remember_derived(con, spec, phrase):
    # SQLCipher ha già aperto e autenticato il file. Conserviamo soltanto la chiave derivata
    # della sessione per evitare una nuova PBKDF2 su ogni ricerca.
    salt = bytes.fromhex(con.execute("PRAGMA cipher_salt").fetchone()[0])
    rounds = int(con.execute("PRAGMA kdf_iter").fetchone()[0])
    if con.execute("PRAGMA cipher_kdf_algorithm").fetchone()[0] != "PBKDF2_HMAC_SHA512":
        raise RuntimeError("Configurazione KDF SQLCipher non supportata.")
    raw = hashlib.pbkdf2_hmac("sha512", phrase, salt, rounds, 32)
    remember(spec, b"x'" + raw.hex().encode("ascii") + b"'")


def recover(path):
    """Elimina una copia intermedia interrotta, senza interferire con una migrazione attiva."""
    stage = Path(str(path) + ".converting")
    if stage.exists():
        lock = Lease(path, exclusive=True)
        try:
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(str(stage) + suffix).unlink(missing_ok=True)
        finally:
            lock.close()


def _sync(path):
    with open(path, "rb") as f:
        os.fsync(f.fileno())


def _sync_dir(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _migrate_vectors(con, path):
    from .db import vector_files
    paths = vector_files(path)
    if not paths:
        return
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='vector_values'").fetchone():
        # L'importazione è transazionale. Possono essere rimasti file vecchi dopo un'interruzione.
        for item in paths:
            item.unlink(missing_ok=True)
        return
    from .models import MODELS, DEFAULT_MODEL
    from .vectors import VectorStore, CHUNK_ROWS
    store = VectorStore(path, MODELS[DEFAULT_MODEL]["dim"])
    with con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("CREATE TABLE IF NOT EXISTS vector_values(id INTEGER PRIMARY KEY, value BLOB NOT NULL)")
        vec, ids = store._maps()
        if ids is not None:
            for start in range(0, len(ids), CHUNK_ROWS):
                con.executemany("INSERT OR REPLACE INTO vector_values VALUES(?,?)",
                                ((int(i), v.tobytes()) for i, v in zip(ids[start:start+CHUNK_ROWS],
                                 vec[start:start+CHUNK_ROWS]) if i >= 0))
        del vec, ids
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='vec_chunks'").fetchone():
            con.execute("DELETE FROM vec_done WHERE doc_id IN (SELECT doc_id FROM vec_chunks "
                        "WHERE id NOT IN (SELECT id FROM vector_values))")
    for item in paths:
        item.unlink(missing_ok=True)


def migrate(path, mode, phrase=None, minutes=10):
    """Le connessioni devono essere chiuse. La testata attiva seleziona le proprie credenziali anche dopo un crash."""
    if mode not in ("plain", "auto", "vault") or not 1 <= minutes <= 1440:
        raise ValueError("Modalità o intervallo di blocco non valido.")
    if mode == "vault" and not phrase:
        raise ValueError("Inserisci una passphrase.")
    path = Path(path)
    driver = cipher()
    lease = Lease(path, exclusive=True)
    stage = Path(str(path) + ".converting")
    old_key, new_key = bytearray(), bytearray()
    account = secrets.token_hex(16)
    created_key = published = False
    descriptor = source = None
    try:
        old = settings(path)
        old_key = _key(path, old)
        with path.open("rb") as f:
            old_header = f.read(16)
        source = _open(path, driver, old_key)
        if source.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise RuntimeError("Attendi la chiusura degli altri lettori dell'archivio.")
        _migrate_vectors(source, path)
        version = source.execute("PRAGMA user_version").fetchone()[0]
        if mode == "auto":
            new_key.extend(b"x'" + secrets.token_hex(32).encode("ascii") + b"'")
            credential("put", account, path.parent, new_key)
            created_key = True
        elif mode == "vault":
            new_key = _phrase_key(phrase)
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(str(stage) + suffix).unlink(missing_ok=True)
        with stage.open("xb") as f:
            os.chmod(f.name, 0o600)
        source.execute("ATTACH DATABASE ? AS converted KEY ?", (str(stage), bytes(new_key)))
        source.execute("SELECT sqlcipher_export('converted')").fetchone()
        source.execute(f"PRAGMA converted.user_version={int(version)}")
        source.execute("DETACH DATABASE converted")
        target = _open(stage, driver, new_key)
        try:
            if new_key:
                target.execute("CREATE TABLE IF NOT EXISTS vector_values(id INTEGER PRIMARY KEY, value BLOB NOT NULL)")
            if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("La copia dell'archivio non supera la verifica di integrità.")
            if new_key and target.execute("PRAGMA cipher_integrity_check").fetchall():
                raise RuntimeError("La verifica delle pagine cifrate è fallita.")
            if target.execute("SELECT 1 FROM sqlite_master WHERE name='fts'").fetchone():
                target.execute("INSERT INTO fts(fts) VALUES('integrity-check')")
            target.commit()
        finally:
            target.close()
        source.close()
        source = None
        spec = {"mode": mode, "id": account, "minutes": minutes}
        if new_key:
            with stage.open("rb") as f:
                descriptor = _descriptor(path, f.read(16))
            with descriptor.open("x", encoding="utf-8") as f:
                json.dump(spec, f)
                f.flush()
                os.fsync(f.fileno())
        _sync(stage)
        _sync_dir(path.parent)
        os.replace(stage, path)
        published = True
        _sync_dir(path.parent)
        forget()
        if mode == "vault":
            opened = _open(path, driver, new_key)
            try:
                _remember_derived(opened, spec, new_key)
            finally:
                opened.close()
        if old["mode"] == "auto":
            credential("delete", old["id"], path.parent)
        if old["mode"] != "plain":
            _descriptor(path, old_header).unlink(missing_ok=True)
    finally:
        if source:
            source.close()
        if not published:
            if created_key:
                credential("delete", account, path.parent)
            if descriptor:
                descriptor.unlink(missing_ok=True)
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(str(stage) + suffix).unlink(missing_ok=True)
        old_key[:] = b"\0" * len(old_key)
        new_key[:] = b"\0" * len(new_key)
        lease.close()
