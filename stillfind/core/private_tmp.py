# SPDX-License-Identifier: GPL-3.0-or-later
"""Spazi temporanei per il contenuto dei documenti, con pulizia dopo un arresto improvviso."""
import os
import re
import shutil
import stat
from contextlib import contextmanager
from functools import lru_cache
from uuid import uuid4

from stillfind.core import config


class PrivateWorkspace:
    """Cartella temporanea privata (solo per l'utente) per i file di lavoro, ripulita anche dopo un arresto improvviso."""

    @staticmethod
    @lru_cache(maxsize=4)
    def _windows_private_acl(path, identity):
        """DACL protetta ed ereditabile, anche con Python Windows precedente a 3.13."""
        import ctypes as c
        from ctypes import wintypes as w
        api = c.WinDLL("advapi32", use_last_error=True)
        descriptor, acl = c.c_void_p(), c.c_void_p()
        present, defaulted = w.BOOL(), w.BOOL()
        convert = api.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.POINTER(w.DWORD)]
        sddl = "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        if not convert(sddl, 1, c.byref(descriptor), None):
            raise c.WinError(c.get_last_error())
        try:
            if not api.GetSecurityDescriptorDacl(descriptor, c.byref(present), c.byref(acl), c.byref(defaulted)):
                raise c.WinError(c.get_last_error())
            apply = api.SetNamedSecurityInfoW
            apply.argtypes = [w.LPWSTR, w.DWORD, w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p]
            code = apply(str(path), 1, 0x80000004, None, None, acl, None)
            if code:
                raise c.WinError(code)
        finally:
            c.windll.kernel32.LocalFree(descriptor)

    @staticmethod
    def root_path():
        return config.APP_DIR / ".ocr-tmp"

    @staticmethod
    def child_environment(tmp):
        return dict(os.environ, TMPDIR=str(tmp), TEMP=str(tmp), TMP=str(tmp), OMP_THREAD_LIMIT="1")

    @staticmethod
    def _root(create):
        root = PrivateWorkspace.root_path()
        if create:
            config.APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            root.mkdir(mode=0o700, exist_ok=True)
        try:
            info = root.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError("La cartella temporanea OCR deve essere una directory privata dell’archivio.")
        if os.name == "nt":
            PrivateWorkspace._windows_private_acl(root, (info.st_dev, info.st_ino))
        else:
            if info.st_uid != os.getuid():
                raise PermissionError("La cartella temporanea OCR appartiene a un altro utente.")
            if info.st_mode & 0o077:
                root.chmod(0o700)
        return root

    @staticmethod
    def _lock(path, *, blocking=False):
        from stillfind.core.locks import ProcessLock
        try:
            info = path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("File di lock OCR non valido.")
        return ProcessLock.acquire(path, blocking=blocking)

    @staticmethod
    @contextmanager
    def workspace():
        root = PrivateWorkspace._root(True)
        # Il lock del singolo lavoro deve esistere prima che la pulizia possa vederlo.
        with PrivateWorkspace._lock(root / ".registro", blocking=True):
            work = root / ("lavoro-" + uuid4().hex)
            work.mkdir(mode=0o700)
            lease = PrivateWorkspace._lock(work / ".attivo", blocking=True)
        try:
            yield work
        finally:
            with PrivateWorkspace._lock(root / ".registro", blocking=True):
                lease.close()
                shutil.rmtree(work)

    @staticmethod
    def cleanup():
        root = PrivateWorkspace._root(False)
        if root is None:
            return
        with PrivateWorkspace._lock(root / ".registro", blocking=True):
            for work in root.glob("lavoro-*"):
                if not re.fullmatch(r"lavoro-[a-f0-9]{32}", work.name):
                    continue
                info = work.lstat()
                if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    continue
                try:
                    lease = PrivateWorkspace._lock(work / ".attivo")
                    if lease is not None:
                        lease.close()
                        shutil.rmtree(work)
                except (PermissionError, ValueError):
                    # Un processo nativo in uscita può ancora tenere aperta una pagina su Windows.
                    # Un file di lock irregolare non deve farci seguire un collegamento.
                    continue
