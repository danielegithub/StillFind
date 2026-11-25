# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Credenziali dell'account. I segreti non transitano in argv, log o configurazione."""
import ctypes as C
import os
import subprocess
import sys
from pathlib import Path

SERVICE = "CercaDoc"


def _mac(action, account, key):
    sec = C.CDLL("/System/Library/Frameworks/Security.framework/Security")
    cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    vp, u32 = C.c_void_p, C.c_uint32
    service, name = SERVICE.encode(), account.encode()
    item, data, size = vp(), vp(), u32()
    args = [None, len(service), service, len(name), name]
    if action == "put":
        fn = sec.SecKeychainAddGenericPassword
        fn.argtypes = [vp, u32, vp, u32, vp, u32, vp, vp]
        rc = fn(*args, len(key), bytes(key), None)
    else:
        find = sec.SecKeychainFindGenericPassword
        find.argtypes = [vp, u32, vp, u32, vp, C.POINTER(u32), C.POINTER(vp), C.POINTER(vp)]
        rc = find(*args, C.byref(size), C.byref(data), C.byref(item))
        if rc == 0:
            try:
                if action == "get":
                    return bytearray(C.string_at(data, size.value))
                rc = sec.SecKeychainItemDelete(item)
            finally:
                sec.SecKeychainItemFreeContent(None, data)
                cf.CFRelease(item)
    if rc and not (action == "delete" and rc == -25300):
        raise RuntimeError(f"Accesso al portachiavi macOS fallito ({rc}).")


class _Blob(C.Structure):
    _fields_ = [("size", C.c_uint32), ("ptr", C.c_void_p)]


def _windows(action, account, key, directory):
    path = directory / (account + ".dpapi")
    if action == "delete":
        path.unlink(missing_ok=True)
        return
    value = bytes(key) if action == "put" else path.read_bytes()
    buf = C.create_string_buffer(value)
    inp, out = _Blob(len(value), C.cast(buf, C.c_void_p)), _Blob()
    crypt = C.WinDLL("crypt32", use_last_error=True)
    fn = crypt.CryptProtectData if action == "put" else crypt.CryptUnprotectData
    if not fn(C.byref(inp), None, None, None, None, 1, C.byref(out)):
        raise C.WinError(C.get_last_error())
    try:
        if action == "get":
            return bytearray(C.string_at(out.ptr, out.size))
        with path.open("xb") as f:
            f.write(C.string_at(out.ptr, out.size))
            f.flush()
            os.fsync(f.fileno())
    finally:
        C.memset(out.ptr, 0, out.size)
        C.windll.kernel32.LocalFree(C.c_void_p(out.ptr))


def credential(action, account, directory, key=None):
    if sys.platform == "darwin":
        return _mac(action, account, key)
    if sys.platform == "win32":
        return _windows(action, account, key, Path(directory))
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if address and any(not a.startswith("unix:") for a in address.split(";") if a):
        raise RuntimeError("Secret Service richiede un bus di sessione locale.")
    op = {"put": ["store", "--label=CercaDoc"], "get": ["lookup"], "delete": ["clear"]}[action]
    try:
        result = subprocess.run(["secret-tool", *op, "application", SERVICE, "archive", account],
                                input=bytes(key) if key is not None else b"", capture_output=True, timeout=60)
    except FileNotFoundError as ex:
        raise RuntimeError("Per il portachiavi Linux occorre secret-tool (libsecret).") from ex
    if result.returncode:
        raise RuntimeError("Il portachiavi Secret Service non è disponibile o è bloccato.")
    if action == "get":
        return bytearray(result.stdout.rstrip(b"\n"))
