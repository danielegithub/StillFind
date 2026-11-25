# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Una combinazione registrata con il sistema, senza intercettare la digitazione."""
import ctypes as C
import json
import os
import re
import subprocess
import sys
import threading
from abc import ABC, abstractmethod

from stillfind.core.config import APP_DIR
from stillfind.runtime.signals import AppSignals


class ShortcutBackend(ABC):
    """Registra Ctrl+Alt+K (⌘+⌥+K su macOS) con il sistema, senza intercettare la digitazione."""

    @staticmethod
    def ready():
        print("READY", flush=True)

    @staticmethod
    def pressed():
        print("OPEN", flush=True)

    @abstractmethod
    def run(self) -> None:
        """Si blocca ascoltando; solleva `RuntimeError` se la combinazione non si può registrare."""


class MacShortcut(ShortcutBackend):
    """Carbon: combinazione registrata con il sistema."""

    def run(self):
        carbon = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        class ID(C.Structure):
            _fields_ = [("sig", C.c_uint32), ("id", C.c_uint32)]
        class Event(C.Structure):
            _fields_ = [("type", C.c_uint32), ("kind", C.c_uint32)]
        handler_type = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)
        @handler_type
        def handler(_next, _event, _ctx):
            self.pressed()
            return 0
        carbon.GetApplicationEventTarget.restype = C.c_void_p
        target = C.c_void_p(carbon.GetApplicationEventTarget())
        event = Event(int.from_bytes(b"keyb", "big"), 5)
        ref, hot = C.c_void_p(), C.c_void_p()
        if carbon.InstallEventHandler(target, handler, 1, C.byref(event), None, C.byref(ref)):
            raise RuntimeError("Registrazione evento macOS fallita.")
        if carbon.RegisterEventHotKey(40, (1 << 8) | (1 << 11), ID(0x43444F43, 1), target, 0, C.byref(hot)):
            raise RuntimeError("Combinazione già in uso.")
        self.ready()
        carbon.RunApplicationEventLoop()


class WindowsShortcut(ShortcutBackend):
    """Win32: `RegisterHotKey` con un ciclo di messaggi."""

    def run(self):
        from ctypes.wintypes import MSG
        user = C.windll.user32
        if not user.RegisterHotKey(None, 1, 0x4003, ord("K")):
            raise RuntimeError("Combinazione già in uso.")
        self.ready()
        msg = MSG()
        try:
            while user.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x312:
                    self.pressed()
        finally:
            user.UnregisterHotKey(None, 1)


class X11Shortcut(ShortcutBackend):
    """X11: `XGrabKey` sulla finestra radice."""

    def run(self):
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
        self.ready()
        event = (C.c_long * 24)()
        while True:
            lib.XNextEvent(display, C.byref(event))
            if C.cast(event, C.POINTER(C.c_int))[0] == 2:
                self.pressed()


class WaylandShortcut(ShortcutBackend):
    """Wayland: portale desktop `GlobalShortcuts` tramite Gio."""

    def run(self):
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
                        self.pressed()
                    return
                method = pending.pop(obj, None)
                if not method:
                    return
                if field(args, 0, as_uint):
                    raise RuntimeError("Il desktop non ha autorizzato la scorciatoia.")
                if method == b"BindShortcuts":
                    self.ready()
                    return
                handle = field(args, 1, lambda v: lookup(v, b"session_handle", None))
                if not handle:
                    raise RuntimeError("Sessione del portale non disponibile.")
                session = as_string(handle, None).decode()
                unref(handle)
                if not re.fullmatch(r"/[a-zA-Z0-9_/]+", session):
                    raise RuntimeError("Sessione del portale non valida.")
                call(b"BindShortcuts", "(objectpath '" + session + "', [('search', {'description': <'Apri StillFind'>,"
                     "'preferred_trigger': <'CTRL+ALT+k'>})], '', @a{sv} {})")
            except Exception as exc:
                failed.append(exc)
                quit_loop(loop)
        subscribe = function(gio, "g_dbus_connection_signal_subscribe", C.c_uint, ptr, string, string, string, string,
                             string, integer, callback, ptr, ptr)
        subscribe(bus, desktop, b"org.freedesktop.portal.Request", b"Response", None, None, 0, on_signal, None, None)
        subscribe(bus, desktop, iface, b"Activated", path, None, 0, on_signal, None, None)
        call(b"CreateSession", "({'session_handle_token': <'stillfind" + str(os.getpid()) + "'>},)")
        function(glib, "g_main_loop_run", None, ptr)(loop)
        if failed:
            raise failed[0]


class ShortcutProcess:
    """Il processo figlio che ascolta la scorciatoia (o ospita l'icona di sistema) e avvisa su stdout."""

    @staticmethod
    def backend() -> "ShortcutBackend":
        """La variante giusta per il sistema in uso."""
        if sys.platform == "darwin":
            return MacShortcut()
        if sys.platform == "win32":
            return WindowsShortcut()
        if os.environ.get("WAYLAND_DISPLAY"):
            return WaylandShortcut()
        return X11Shortcut()

    @staticmethod
    def listen(tray=False):
        # EOF sulla pipe del genitore libera la registrazione anche dopo un arresto improvviso.
        def parent_closed():
            if tray:
                from stillfind.runtime.tray import TrayMenu
                for line in sys.stdin:
                    TrayMenu.status = json.loads(line)
            else:
                os.read(sys.stdin.fileno(), 1)
            if tray and sys.platform == "win32":
                from stillfind.runtime.tray import TrayMenu
                TrayMenu.remove_icon()
            os._exit(0)
        threading.Thread(target=parent_closed, daemon=True).start()
        try:
            if tray:
                from stillfind.runtime.tray import TrayIcon
                TrayIcon.run()
            else:
                ShortcutProcess.backend().run()
        except Exception as exc:
            print("ERROR " + str(exc), flush=True)
            return 1
        return 0


    @staticmethod
    def start(on_open, on_error=lambda text: None, tray=False):
        proc = subprocess.Popen(AppSignals.command("--tray-search" if tray else "--listen-shortcut"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        def events():
            for line in proc.stdout:
                if line.strip() == "OPEN":
                    on_open()
                elif tray and line.strip() in AppSignals.TRAY_ACTIONS:
                    on_open(line.strip())
                elif line.startswith("ERROR "):
                    on_error(line[6:].strip())
            proc.stdout.close()
        threading.Thread(target=events, daemon=True).start()
        return proc


class Registration:
    """Finestra e agente si alternano nella proprietà della stessa combinazione."""
    def __init__(self, enabled, on_open, on_error, tray=False):
        from stillfind.core.locks import ProcessLock
        self.stopped = threading.Event()

        def monitor():
            while not self.stopped.is_set():
                lock = ProcessLock.acquire(APP_DIR / ("tray.lock" if tray else "shortcut.lock")) if enabled() else None
                if lock is None:
                    self.stopped.wait(2)
                    continue
                proc = None
                try:
                    proc = ShortcutProcess.start(on_open, on_error, tray)
                    last = ""
                    while proc.poll() is None and enabled() and not self.stopped.is_set():
                        if tray:
                            try:
                                st = tray()
                                value = [st["indexed"], st.get("ocr_pending", 0), st["manual_pause"]] if st else None
                            except Exception:
                                value = None
                            line = json.dumps(value)
                            if last != line:
                                proc.stdin.write(line + "\n")
                                proc.stdin.flush()
                                last = line
                        self.stopped.wait(1)
                except OSError as exc:
                    on_error(str(exc))
                finally:
                    if proc:
                        try:
                            proc.stdin.close()
                        except OSError:
                            pass
                        try:
                            proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                    lock.close()
                if enabled():  # se la finestra ha preso il controllo, ritenta appena lo rilascia
                    self.stopped.wait(60 if tray else 10)
        self.thread = threading.Thread(target=monitor, daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.thread.join(4)
