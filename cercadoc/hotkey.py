# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Una combinazione registrata con il sistema, senza intercettare la digitazione."""
import ctypes as C
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from .config import APP_DIR

LABEL = "⌘+⌥+K" if sys.platform == "darwin" else "Ctrl+Alt+K"
SIGNAL = APP_DIR / "apri-ricerca"


def command(*args):
    return [sys.executable, *([] if getattr(sys, "frozen", False) else ["-m", "cercadoc"]), *args]


def signal_window():
    SIGNAL.touch(mode=0o600)


def show():
    from .agent import acquire_lock
    lock = acquire_lock(APP_DIR / "window.lock")
    if lock is None:
        signal_window()
    else:
        lock.close()
        subprocess.Popen(command(), stdin=subprocess.DEVNULL)


def _ready():
    print("READY", flush=True)


def _pressed():
    print("OPEN", flush=True)


def _mac():
    carbon = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
    class ID(C.Structure):
        _fields_ = [("sig", C.c_uint32), ("id", C.c_uint32)]
    class Event(C.Structure):
        _fields_ = [("type", C.c_uint32), ("kind", C.c_uint32)]
    handler_type = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)
    @handler_type
    def handler(_next, _event, _ctx):
        _pressed()
        return 0
    carbon.GetApplicationEventTarget.restype = C.c_void_p
    target = C.c_void_p(carbon.GetApplicationEventTarget())
    event = Event(int.from_bytes(b"keyb", "big"), 5)
    ref, hot = C.c_void_p(), C.c_void_p()
    if carbon.InstallEventHandler(target, handler, 1, C.byref(event), None, C.byref(ref)):
        raise RuntimeError("Registrazione evento macOS fallita.")
    if carbon.RegisterEventHotKey(40, (1 << 8) | (1 << 11), ID(0x43444F43, 1), target, 0, C.byref(hot)):
        raise RuntimeError("Combinazione già in uso.")
    _ready()
    carbon.RunApplicationEventLoop()


def _windows():
    from ctypes.wintypes import MSG
    user = C.windll.user32
    if not user.RegisterHotKey(None, 1, 0x4003, ord("K")):
        raise RuntimeError("Combinazione già in uso.")
    _ready()
    msg = MSG()
    try:
        while user.GetMessageW(C.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x312:
                _pressed()
    finally:
        user.UnregisterHotKey(None, 1)


def _x11():
    if not re.match(r"^(?::|unix:|unix/:)\d+(\.\d+)?$", os.environ.get("DISPLAY", "")):
        raise RuntimeError("È richiesto un display X11 locale.")
    lib = C.CDLL("libX11.so.6")
    lib.XOpenDisplay.restype = C.c_void_p
    raw = lib.XOpenDisplay(None)
    if not raw:
        raise RuntimeError("Display X11 non disponibile.")
    display = C.c_void_p(raw)
    lib.XDefaultRootWindow.restype = C.c_ulong
    root = C.c_ulong(lib.XDefaultRootWindow(display))
    key = lib.XKeysymToKeycode(display, ord("k"))
    failures = []
    @C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
    def on_error(*_):
        failures.append(True)
        return 0
    lib.XSetErrorHandler(on_error)
    for locks in (0, 2, 16, 18):
        lib.XGrabKey(display, key, 12 | locks, root, 0, 1, 1)
    lib.XSync(display, 0)
    if failures:
        raise RuntimeError("Combinazione già in uso.")
    _ready()
    event = (C.c_long * 24)()
    while True:
        lib.XNextEvent(display, C.byref(event))
        if C.cast(event, C.POINTER(C.c_int))[0] == 2:
            _pressed()


def _wayland():
    # Usa direttamente Gio del desktop anche nella distribuzione Python autonoma.
    if any(not a.startswith("unix:") for a in os.environ.get("DBUS_SESSION_BUS_ADDRESS", "").split(";") if a):
        raise RuntimeError("Bus di sessione non locale.")
    gio, glib = C.CDLL("libgio-2.0.so.0"), C.CDLL("libglib-2.0.so.0")
    ptr, string, integer = C.c_void_p, C.c_char_p, C.c_int
    def function(lib, name, result, *args):
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, result
        return fn
    bus = function(gio, "g_bus_get_sync", ptr, integer, ptr, ptr)(2, None, None)
    if not bus:
        raise RuntimeError("Bus locale della sessione non disponibile.")
    parse = function(glib, "g_variant_parse", ptr, ptr, string, ptr, ptr, ptr)
    child = function(glib, "g_variant_get_child_value", ptr, ptr, C.c_size_t)
    unref = function(glib, "g_variant_unref", None, ptr)
    as_string = function(glib, "g_variant_get_string", string, ptr, ptr)
    as_uint = function(glib, "g_variant_get_uint32", C.c_uint, ptr)
    lookup = function(glib, "g_variant_lookup_value", ptr, ptr, string, ptr)
    call_sync = function(gio, "g_dbus_connection_call_sync", ptr, ptr, string, string, string, string,
                         ptr, ptr, integer, integer, ptr, ptr)
    desktop, path = b"org.freedesktop.portal.Desktop", b"/org/freedesktop/portal/desktop"
    iface = b"org.freedesktop.portal.GlobalShortcuts"
    loop = function(glib, "g_main_loop_new", ptr, ptr, integer)(None, 0)
    quit_loop = function(glib, "g_main_loop_quit", None, ptr)
    pending, failed = {}, []
    def field(value, index, fn):
        part = child(value, index)
        try:
            return fn(part)
        finally:
            unref(part)
    def call(method, args):
        params = parse(None, args.encode(), None, None, None)
        if not params:
            raise RuntimeError("Parametri del portale non validi.")
        response = call_sync(bus, desktop, path, iface, method, params, None, 0, 5000, None, None)
        if not response:
            raise RuntimeError("Il desktop non offre il portale per le scorciatoie.")
        pending[field(response, 0, lambda v: as_string(v, None))] = method
        unref(response)
    callback = C.CFUNCTYPE(None, ptr, string, string, string, string, ptr, ptr)
    @callback
    def on_signal(_bus, _sender, obj, _iface, signal, args, _data):
        try:
            if signal == b"Activated":
                if field(args, 1, lambda v: as_string(v, None)) == b"search":
                    _pressed()
                return
            method = pending.pop(obj, None)
            if not method:
                return
            if field(args, 0, as_uint):
                raise RuntimeError("Il desktop non ha autorizzato la scorciatoia.")
            if method == b"BindShortcuts":
                _ready()
                return
            handle = field(args, 1, lambda v: lookup(v, b"session_handle", None))
            if not handle:
                raise RuntimeError("Sessione del portale non disponibile.")
            session = as_string(handle, None).decode()
            unref(handle)
            if not re.fullmatch(r"/[a-zA-Z0-9_/]+", session):
                raise RuntimeError("Sessione del portale non valida.")
            call(b"BindShortcuts", "(objectpath '" + session + "', [('search', {'description': <'Apri CercaDoc'>,"
                 "'preferred_trigger': <'CTRL+ALT+k'>})], '', @a{sv} {})")
        except Exception as exc:
            failed.append(exc)
            quit_loop(loop)
    subscribe = function(gio, "g_dbus_connection_signal_subscribe", C.c_uint, ptr, string, string, string, string,
                         string, integer, callback, ptr, ptr)
    subscribe(bus, desktop, b"org.freedesktop.portal.Request", b"Response", None, None, 0, on_signal, None, None)
    subscribe(bus, desktop, iface, b"Activated", path, None, 0, on_signal, None, None)
    call(b"CreateSession", "({'session_handle_token': <'cercadoc" + str(os.getpid()) + "'>},)")
    function(glib, "g_main_loop_run", None, ptr)(loop)
    if failed:
        raise failed[0]


def listen(tray=False):
    # EOF sulla pipe del genitore libera la registrazione anche dopo un arresto improvviso.
    def parent_closed():
        os.read(sys.stdin.fileno(), 1)
        if tray and sys.platform == "win32":
            from .tray import remove_icon
            remove_icon()
        os._exit(0)
    threading.Thread(target=parent_closed, daemon=True).start()
    try:
        if tray:
            from .tray import run
            run()
        elif sys.platform == "darwin":
            _mac()
        elif sys.platform == "win32":
            _windows()
        elif os.environ.get("WAYLAND_DISPLAY"):
            _wayland()
        else:
            _x11()
    except Exception as exc:
        print("ERROR " + str(exc), flush=True)
        return 1
    return 0


def start(on_open, on_error=lambda text: None, tray=False):
    proc = subprocess.Popen(command("--tray-search" if tray else "--listen-shortcut"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    def events():
        for line in proc.stdout:
            if line.strip() == "OPEN":
                on_open()
            elif line.startswith("ERROR "):
                on_error(line[6:].strip())
        proc.stdout.close()
    threading.Thread(target=events, daemon=True).start()
    return proc


class Registration:
    """Finestra e agente si alternano nella proprietà della stessa combinazione."""
    def __init__(self, enabled, on_open, on_error, tray=False):
        from .agent import acquire_lock
        self.stopped = threading.Event()

        def monitor():
            while not self.stopped.is_set():
                lock = acquire_lock(APP_DIR / ("tray.lock" if tray else "shortcut.lock")) if enabled() else None
                if lock is None:
                    self.stopped.wait(2)
                    continue
                proc = None
                try:
                    proc = start(on_open, on_error, tray)
                    while proc.poll() is None and enabled() and not self.stopped.wait(1):
                        pass
                except OSError as exc:
                    on_error(str(exc))
                finally:
                    if proc:
                        proc.stdin.close()
                        try:
                            proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                    lock.close()
                self.stopped.wait(60 if tray else 10)
        self.thread = threading.Thread(target=monitor, daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.thread.join(4)
