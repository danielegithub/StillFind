# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Apertura centralizzata e migrazione atomica dell'archivio locale."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import weakref
from pathlib import Path

from stillfind.core.i18n import tr
from stillfind.storage.credentials import LEGACY_SERVICE, SERVICE, CredentialStore
from stillfind.storage.database import Database

CLEAR = b"SQLite format 3\0"


class Locked(RuntimeError):
    pass


class _ArchiveSettings:
    def __init__(self, path):
        self.spec = ArchiveProtection._read_settings(path)


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
            raise RuntimeError(tr("Archivio occupato. Attendi la fine delle altre operazioni e riprova.")) from exc

    def close(self):
        self.file.close()
        self.archive_settings = None


class ArchiveProtection:
    """Apertura centralizzata dell'archivio: modalità (semplice, portachiavi, cassaforte), chiavi e migrazione."""

    CLEAR = CLEAR
    _session = bytearray()
    _session_id = None
    _secret = bytearray()
    _secret_id = None
    _mutex = threading.RLock()
    _open_settings = weakref.WeakValueDictionary()

    @classmethod
    def forget(cls):
        """Dimentica la chiave della sessione (cassaforte)."""
        with cls._mutex:
            cls._session[:] = b"\0" * len(cls._session)
            cls._session.clear()
            cls._session_id = None
            cls.hide_secret()

    @classmethod
    def hide_secret(cls):
        """Azzera il segreto visualizzabile; la chiave derivata resta utilizzabile fino al blocco."""
        with cls._mutex:
            cls._secret[:] = b"\0" * len(cls._secret)
            cls._secret.clear()
            cls._secret_id = None

    @classmethod
    def remember_secret(cls, spec, secret):
        with cls._mutex:
            cls.hide_secret()
            if spec["mode"] != "plain":
                cls._secret.extend(secret)
                cls._secret_id = spec["id"]

    @classmethod
    def visible_secret(cls, path):
        """Copia temporanea, da azzerare dopo l'uso. Non recupera un segreto nascosto."""
        spec = cls.settings(path)
        with cls._mutex:
            if cls._secret_id != spec.get("id") or not cls._secret:
                raise Locked(tr("Reinserisci la password attuale per mostrarla."))
            return cls._secret.copy()

    @classmethod
    def prepare_visibility(cls, path, hidden):
        """All'avvio UI recupera la chiave automatica solo se la visibilità è consentita."""
        spec = cls.settings(path)
        if hidden or spec["mode"] == "plain":
            cls.hide_secret()
        elif spec["mode"] == "auto":
            key = cls._key(Path(path), spec)
            try:
                cls.remember_secret(spec, memoryview(key)[2:-1])
            finally:
                key[:] = b"\0" * len(key)

    @classmethod
    def verify_secret(cls, path, secret):
        """Verifica contro SQLCipher senza cambiare sessione o visibilità.

        La modalità Portachiavi usa i 64 caratteri esadecimali mostrati nella UI;
        la Cassaforte usa la passphrase originale, mai la chiave derivata.
        """
        spec = cls.settings(path)
        if spec["mode"] == "plain":
            raise Locked(tr("L’archivio non è cifrato."))
        if spec["mode"] == "auto":
            if not re.fullmatch(rb"[0-9a-f]{64}", secret):
                raise Locked(tr("Chiave errata: inserisci i 64 caratteri esadecimali salvati."))
            key = bytearray(b"x'")
            key.extend(secret)
            key.extend(b"'")
        else:
            key = cls._phrase_key(secret)
        lease = None
        try:
            lease = Lease(path)
            # Riusa la descrizione già letta: non riapre il file DB e non altera i lock SQLite.
            lease.archive_settings = cls._archive_settings(path)
            try:
                con = cls._open(path, cls.cipher(), key, lease, readonly=True)
            except cls.cipher().DatabaseError as exc:
                raise Locked(tr("Password errata o archivio illeggibile.")) from exc
            con.close()
        finally:
            key[:] = b"\0" * len(key)
            if lease is not None:
                lease.close()

    @staticmethod
    def cipher():
        try:
            import sqlcipher3
        except ImportError as exc:
            raise RuntimeError(tr("Il componente locale sqlcipher3 non è installato.")) from exc
        return sqlcipher3

    @staticmethod
    def _descriptor(path, salt):
        return Path(str(path) + ".key-" + salt.hex() + ".json")

    @classmethod
    def _archive_settings(cls, path):
        # Su POSIX chiudere un secondo fd sul DB libera i lock SQLite dell'intero
        # processo. Leggiamo la testata prima della prima connessione e riusiamo la
        # modalità finché almeno una connessione tiene aperto lo stesso archivio.
        path = Path(path).resolve()
        with cls._mutex:
            active = cls._open_settings.get(path)
            if active is None:
                active = _ArchiveSettings(path)
                cls._open_settings[path] = active
            return active

    @classmethod
    def settings(cls, path):
        return cls._archive_settings(path).spec.copy()

    @classmethod
    def _read_settings(cls, path):
        try:
            with Path(path).open("rb") as f:
                prefix = f.read(16)
        except FileNotFoundError:
            prefix = b""
        if prefix in (b"", CLEAR):
            return {"mode": "plain"}
        try:
            data = json.loads(cls._descriptor(path, prefix).read_text("utf-8"))
            if data["mode"] in ("auto", "vault") and re.fullmatch("[0-9a-f]{32}", data["id"]):
                return data
        except (OSError, ValueError, KeyError, TypeError):
            pass
        raise Locked(tr("Mancano le impostazioni di apertura del database cifrato."))

    @classmethod
    def remember(cls, spec, key):
        with cls._mutex:
            cls.forget()
            cls._session.extend(key)
            cls._session_id = spec["id"]

    @classmethod
    def session_key(cls, spec):
        with cls._mutex:
            if cls._session_id != spec["id"] or not cls._session:
                raise Locked(tr("Cassaforte bloccata. Apri StillFind e inserisci la passphrase."))
            return cls._session.copy()

    @classmethod
    def _key(cls, path, spec):
        if spec["mode"] == "vault":
            return cls.session_key(spec)
        if spec["mode"] == "auto":
            service = SERVICE if spec.get("credential_service") == SERVICE else LEGACY_SERVICE
            key = CredentialStore.access("get", spec["id"], path.parent, service=service)
            if not key or not re.fullmatch(rb"x'[0-9a-f]{64}'", key):
                raise Locked(tr("La chiave dell’archivio non è disponibile nel portachiavi."))
            return key
        return bytearray()

    @staticmethod
    def _open(path, driver, key, lease=None, readonly=False):
        class Connection(Database, driver.Connection):
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
                    raise RuntimeError(tr("SQLCipher non disponibile."))
            con.execute("PRAGMA temp_store=MEMORY")
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            return con
        except BaseException:
            con.close()
            raise

    @classmethod
    def connection(cls, path, readonly=False):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        cls.recover(path)
        lease = Lease(path)
        key = bytearray()
        try:
            lease.archive_settings = cls._archive_settings(path)
            spec = lease.archive_settings.spec
            key = cls._key(path, spec)
            return cls._open(path, cls.cipher() if key else sqlite3, key, lease, readonly)
        except BaseException:
            lease.close()
            raise
        finally:
            key[:] = b"\0" * len(key)

    @classmethod
    def unlock(cls, path, phrase, *, hide_secret=True):
        spec = cls.settings(path)
        if spec["mode"] != "vault":
            raise RuntimeError(tr("L'archivio non usa una passphrase."))
        key = cls._phrase_key(phrase)
        try:
            con = cls._open(path, cls.cipher(), key)
            try:
                cls._remember_derived(con, spec, key)
                if not hide_secret:
                    cls.remember_secret(spec, memoryview(key)[len(b"CercaDoc:"):])
            finally:
                con.close()
        except cls.cipher().DatabaseError as exc:
            raise Locked(tr("Passphrase errata o archivio illeggibile.")) from exc
        finally:
            key[:] = b"\0" * len(key)

    @staticmethod
    def _phrase_key(phrase):
        # Parte del formato della cassaforte. Cambiarlo renderebbe illeggibili gli archivi esistenti.
        return bytearray(b"CercaDoc:") + (phrase.encode("utf-8") if isinstance(phrase, str) else phrase)

    @classmethod
    def _remember_derived(cls, con, spec, phrase):
        # SQLCipher ha già aperto e autenticato il file. Conserviamo soltanto la chiave derivata
        # della sessione per evitare una nuova PBKDF2 su ogni ricerca.
        salt = bytes.fromhex(con.execute("PRAGMA cipher_salt").fetchone()[0])
        rounds = int(con.execute("PRAGMA kdf_iter").fetchone()[0])
        if con.execute("PRAGMA cipher_kdf_algorithm").fetchone()[0] != "PBKDF2_HMAC_SHA512":
            raise RuntimeError(tr("Configurazione KDF SQLCipher non supportata."))
        raw = hashlib.pbkdf2_hmac("sha512", phrase, salt, rounds, 32)
        cls.remember(spec, b"x'" + raw.hex().encode("ascii") + b"'")

    @staticmethod
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

    @staticmethod
    def _sync(path):
        with open(path, "rb") as f:
            os.fsync(f.fileno())

    @staticmethod
    def _sync_dir(path):
        if os.name != "nt":
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    @staticmethod
    def _migrate_vectors(con, path):
        from stillfind.storage.vector_files import VectorFiles
        paths = VectorFiles.existing(path)
        if not paths:
            return
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='vector_values'").fetchone():
            # L'importazione è transazionale. Possono essere rimasti file vecchi dopo un'interruzione.
            for item in paths:
                item.unlink(missing_ok=True)
            return
        from stillfind.semantic.models import MODELS, DEFAULT_MODEL
        from stillfind.storage.vectors import VectorStore, CHUNK_ROWS
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

    @classmethod
    def migrate(cls, path, mode, phrase=None, minutes=10, *, hide_secret=True):
        """Le connessioni devono essere chiuse. La testata attiva seleziona le proprie credenziali anche dopo un crash."""
        if mode not in ("plain", "auto", "vault") or not 1 <= minutes <= 1440:
            raise ValueError(tr("Modalità o intervallo di blocco non valido."))
        if mode == "vault" and not phrase:
            raise ValueError(tr("Inserisci una passphrase."))
        path = Path(path)
        driver = cls.cipher()
        lease = Lease(path, exclusive=True)
        stage = Path(str(path) + ".converting")
        old_key, new_key = bytearray(), bytearray()
        account = secrets.token_hex(16)
        created_key = published = False
        descriptor = source = None
        try:
            old = cls.settings(path)
            old_key = cls._key(path, old)
            with path.open("rb") as f:
                old_header = f.read(16)
            source = cls._open(path, driver, old_key)
            if source.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise RuntimeError(tr("Attendi la chiusura degli altri lettori dell'archivio."))
            cls._migrate_vectors(source, path)
            version = source.execute("PRAGMA user_version").fetchone()[0]
            if mode == "auto":
                new_key.extend(b"x'" + secrets.token_hex(32).encode("ascii") + b"'")
                CredentialStore.access("put", account, path.parent, new_key)
                created_key = True
            elif mode == "vault":
                new_key = cls._phrase_key(phrase)
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(str(stage) + suffix).unlink(missing_ok=True)
            with stage.open("xb") as f:
                os.chmod(f.name, 0o600)
            source.execute("ATTACH DATABASE ? AS converted KEY ?", (str(stage), bytes(new_key)))
            source.execute("SELECT sqlcipher_export('converted')").fetchone()
            source.execute(f"PRAGMA converted.user_version={int(version)}")
            source.execute("DETACH DATABASE converted")
            target = cls._open(stage, driver, new_key)
            try:
                if new_key:
                    target.execute("CREATE TABLE IF NOT EXISTS vector_values(id INTEGER PRIMARY KEY, value BLOB NOT NULL)")
                if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError(tr("La copia dell'archivio non supera la verifica di integrità."))
                if new_key and target.execute("PRAGMA cipher_integrity_check").fetchall():
                    raise RuntimeError(tr("La verifica delle pagine cifrate è fallita."))
                if target.execute("SELECT 1 FROM sqlite_master WHERE name='fts'").fetchone():
                    target.execute("INSERT INTO fts(fts) VALUES('integrity-check')")
                target.commit()
            finally:
                target.close()
            source.close()
            source = None
            spec = {"mode": mode, "id": account, "minutes": minutes}
            if mode == "auto":
                spec["credential_service"] = SERVICE
            if new_key:
                with stage.open("rb") as f:
                    descriptor = cls._descriptor(path, f.read(16))
                with descriptor.open("x", encoding="utf-8") as f:
                    json.dump(spec, f)
                    f.flush()
                    os.fsync(f.fileno())
            cls._sync(stage)
            cls._sync_dir(path.parent)
            os.replace(stage, path)
            published = True
            cls._sync_dir(path.parent)
            cls.forget()
            if mode == "vault":
                opened = cls._open(path, driver, new_key)
                try:
                    cls._remember_derived(opened, spec, new_key)
                    if not hide_secret:
                        cls.remember_secret(spec, memoryview(new_key)[len(b"CercaDoc:"):])
                finally:
                    opened.close()
            elif mode == "auto" and not hide_secret:
                cls.remember_secret(spec, memoryview(new_key)[2:-1])
            if old["mode"] == "auto":
                service = SERVICE if old.get("credential_service") == SERVICE else LEGACY_SERVICE
                CredentialStore.access("delete", old["id"], path.parent, service=service)
            if old["mode"] != "plain":
                cls._descriptor(path, old_header).unlink(missing_ok=True)
        finally:
            if source:
                source.close()
            if not published:
                if created_key:
                    CredentialStore.access("delete", account, path.parent)
                if descriptor:
                    descriptor.unlink(missing_ok=True)
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(str(stage) + suffix).unlink(missing_ok=True)
            old_key[:] = b"\0" * len(old_key)
            new_key[:] = b"\0" * len(new_key)
            lease.close()
