# SPDX-License-Identifier: GPL-3.0-or-later
"""Menu nativo della sessione. Il genitore invia solo i contatori tramite la pipe locale."""
import ctypes as C
import os
import sys
from abc import ABC, abstractmethod

from stillfind.core.i18n import tr
from stillfind.runtime.hotkey import ShortcutBackend
from stillfind.runtime.signals import AppSignals


class TrayMenu:
    """Voci del menu e azioni: lo stato arriva dal processo genitore tramite la pipe locale."""

    status = None  # [file indicizzati, OCR in coda, in pausa] oppure None

    @classmethod
    def entries(cls):
        snapshot = cls.status
        counts = (tr("{files} file indicizzati · {ocr} OCR in coda", files=snapshot[0], ocr=snapshot[1])
                  if snapshot is not None else tr("Stato archivio non disponibile"))
        paused = snapshot is not None and snapshot[2]
        return [("open", tr("Cerca…")), (None, counts), ("resume", tr("Riprendi")) if paused else ("pause", tr("Pausa")),
                ("settings", tr("Impostazioni")), ("about", tr("Informazioni")), ("quit", tr("Esci"))]

    @staticmethod
    def activate(action):
        if action:
            if action in ("open", "settings", "about"):
                AppSignals.allow_window_focus()
            print(action, flush=True)

    @staticmethod
    def remove_icon():
        """Sostituito dalla variante Windows, che deve togliere l'icona all'uscita."""


class TrayBackend(ABC):
    """L'icona di sistema sulla piattaforma in uso."""

    @abstractmethod
    def run(self) -> None:
        """Mostra l'icona e resta in ascolto dei clic."""


class MacTray(TrayBackend):
    """Barra dei menu di macOS (AppKit)."""

    def run(self):
        from ctypes.util import find_library
        C.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc = C.CDLL(find_library("objc"))
        ptr, string = C.c_void_p, C.c_char_p
        objc.objc_getClass.argtypes, objc.objc_getClass.restype = [string], ptr
        objc.sel_registerName.argtypes, objc.sel_registerName.restype = [string], ptr
        objc.objc_allocateClassPair.argtypes, objc.objc_allocateClassPair.restype = [ptr, string, C.c_size_t], ptr
        objc.class_addMethod.argtypes = [ptr, ptr, ptr, string]
        objc.objc_registerClassPair.argtypes = [ptr]
        def send(receiver, name, result=ptr, argtypes=(), args=()):
            fn = C.CFUNCTYPE(result, ptr, ptr, *argtypes)(("objc_msgSend", objc))
            return fn(receiver, objc.sel_registerName(name.encode()), *args)
        def cls(name):
            return objc.objc_getClass(name.encode())
        def ns(text):
            return send(cls("NSString"), "stringWithUTF8String:", ptr, [string], [text.encode()])
        pool = send(send(cls("NSAutoreleasePool"), "alloc"), "init")
        app = send(cls("NSApplication"), "sharedApplication")
        send(app, "setActivationPolicy:", C.c_bool, [C.c_long], [1])
        menu = send(send(cls("NSMenu"), "alloc"), "init")
        send(menu, "setAutoenablesItems:", None, [C.c_bool], [False])
        items, actions = [], []
        callback = C.CFUNCTYPE(None, ptr, ptr, ptr)

        @callback
        def clicked(_self, _cmd, sender):
            TrayMenu.activate(actions[send(sender, "tag", C.c_long)])

        @callback
        def refresh(_self, _cmd, _menu):
            for i, (action, title) in enumerate(TrayMenu.entries()):
                actions[i] = action
                send(items[i], "setTitle:", None, [ptr], [ns(title)])

        action = objc.sel_registerName(b"selectItem:")
        handler = objc.objc_allocateClassPair(cls("NSObject"), b"StillFindTrayAction", 0)
        objc.class_addMethod(handler, action, clicked, b"v@:@")
        objc.class_addMethod(handler, objc.sel_registerName(b"menuNeedsUpdate:"), refresh, b"v@:@")
        objc.objc_registerClassPair(handler)
        target = send(send(handler, "alloc"), "init")
        send(menu, "setDelegate:", None, [ptr], [target])
        for i, (command, title) in enumerate(TrayMenu.entries()):
            entry = send(menu, "addItemWithTitle:action:keyEquivalent:", ptr, [ptr, ptr, ptr],
                         [ns(title), action if command else None, ns("")])
            send(entry, "setTarget:", None, [ptr], [target])
            send(entry, "setTag:", None, [C.c_long], [i])
            send(entry, "setEnabled:", None, [C.c_bool], [bool(command)])
            items.append(entry)
            actions.append(command)
        bar = send(cls("NSStatusBar"), "systemStatusBar")
        item = send(bar, "statusItemWithLength:", ptr, [C.c_double], [-1.0])
        button = send(item, "button")
        send(button, "setTitle:", None, [ptr], [ns("⌕")])
        send(button, "setToolTip:", None, [ptr], [ns("StillFind")])
        send(item, "setMenu:", None, [ptr], [menu])
        ShortcutBackend.ready()
        send(app, "run", None)
        send(pool, "drain", None)


class WindowsTray(TrayBackend):
    """Area di notifica di Windows."""

    def run(self):
        from ctypes import wintypes as W
        user, shell, kernel = C.windll.user32, C.windll.shell32, C.windll.kernel32
        msg_tray = 0x8001
        callback = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        class WindowClass(C.Structure):
            _fields_ = [("style", W.UINT), ("proc", callback), ("class_extra", C.c_int), ("window_extra", C.c_int),
                        ("instance", W.HINSTANCE), ("icon", W.HICON), ("cursor", W.HANDLE), ("background", W.HBRUSH),
                        ("menu", W.LPCWSTR), ("name", W.LPCWSTR)]
        class Icon(C.Structure):
            # Layout NOTIFYICONDATAW V1, riconosciuto anche dalle versioni moderne di Explorer.
            _fields_ = [("size", W.DWORD), ("window", W.HWND), ("id", W.UINT), ("flags", W.UINT),
                        ("message", W.UINT), ("icon", W.HICON), ("tip", W.WCHAR * 64)]
        shell.Shell_NotifyIconW.argtypes = [W.DWORD, C.POINTER(Icon)]
        user.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        user.DefWindowProcW.restype = C.c_ssize_t
        restart = user.RegisterWindowMessageW("TaskbarCreated")
        user.CreatePopupMenu.restype = W.HMENU
        user.AppendMenuW.argtypes = [W.HMENU, W.UINT, C.c_size_t, W.LPCWSTR]
        user.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, C.c_void_p]
        user.SetForegroundWindow.argtypes = [W.HWND]
        user.DestroyMenu.argtypes = [W.HMENU]
        user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]

        def popup(hwnd):
            menu = user.CreatePopupMenu()
            choices = TrayMenu.entries()
            try:
                for i, (action, title) in enumerate(choices, 1):
                    user.AppendMenuW(menu, 0 if action else 1, i, title)
                pos = W.POINT()
                user.GetCursorPos(C.byref(pos))
                user.SetForegroundWindow(hwnd)
                selected = user.TrackPopupMenu(menu, 0x102, pos.x, pos.y, 0, hwnd, None)
                if selected:
                    TrayMenu.activate(choices[selected - 1][0])
                user.PostMessageW(hwnd, 0, 0, 0)
            finally:
                user.DestroyMenu(menu)

        @callback
        def receive(hwnd, event, wparam, lparam):
            if event == msg_tray and lparam in (0x202, 0x205):  # rilascio del pulsante sinistro o destro
                popup(hwnd)
                return 0
            if event == restart:
                shell.Shell_NotifyIconW(0, C.byref(icon))
                return 0
            return user.DefWindowProcW(hwnd, event, wparam, lparam)
        kernel.GetModuleHandleW.restype = W.HMODULE
        instance = kernel.GetModuleHandleW(None)
        wc = WindowClass()
        wc.proc, wc.instance, wc.name = receive, instance, "StillFindTray"
        if not user.RegisterClassW(C.byref(wc)):
            raise C.WinError()
        user.CreateWindowExW.restype = W.HWND
        user.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int,
                                         C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p]
        hwnd = user.CreateWindowExW(0, wc.name, "", 0, 0, 0, 0, 0, None, None, instance, None)
        if not hwnd:
            raise C.WinError()
        user.LoadIconW.restype = W.HICON
        icon = Icon(C.sizeof(Icon), hwnd, 1, 7, msg_tray, user.LoadIconW(None, C.c_void_p(32512)), "StillFind")
        if not shell.Shell_NotifyIconW(0, C.byref(icon)):
            raise C.WinError()
        TrayMenu.remove_icon = lambda: shell.Shell_NotifyIconW(2, C.byref(icon))
        ShortcutBackend.ready()
        msg = W.MSG()
        try:
            while user.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                user.TranslateMessage(C.byref(msg))
                user.DispatchMessageW(C.byref(msg))
        finally:
            TrayMenu.remove_icon()


class LinuxTray(TrayBackend):
    """AppIndicator su Linux."""

    def run(self):
        # Gtk e AppIndicator usano solo il display e il bus della sessione locale.
        import re
        from ctypes.util import find_library
        if any(not a.startswith("unix:") for a in os.environ.get("DBUS_SESSION_BUS_ADDRESS", "").split(";") if a):
            raise RuntimeError("È richiesto un bus di sessione locale.")
        if not os.environ.get("WAYLAND_DISPLAY") and not re.fullmatch(r"(?::|unix:|unix/:)\d+(\.\d+)?", os.environ.get("DISPLAY", "")):
            raise RuntimeError("È richiesto un display locale.")
        name = find_library("ayatana-appindicator3") or find_library("appindicator3")
        if not name:
            raise RuntimeError("AppIndicator non disponibile.")
        indicator = C.CDLL(name)
        gtk, gobject = C.CDLL("libgtk-3.so.0"), C.CDLL("libgobject-2.0.so.0")
        if not gtk.gtk_init_check(None, None):
            raise RuntimeError("Sessione grafica non disponibile.")
        ptr = C.c_void_p
        indicator.app_indicator_new.restype = ptr
        instance = ptr(indicator.app_indicator_new(b"stillfind", b"system-search", 0))
        gtk.gtk_menu_new.restype = gtk.gtk_menu_item_new_with_label.restype = ptr
        menu = ptr(gtk.gtk_menu_new())
        items, actions = [], []
        @C.CFUNCTYPE(None, ptr, ptr)
        def clicked(_item, data):
            TrayMenu.activate(actions[data - 1])

        previous = TrayMenu.entries()
        for i, (action, title) in enumerate(previous, 1):
            item = ptr(gtk.gtk_menu_item_new_with_label(title.encode()))
            items.append(item)
            actions.append(action)
            gtk.gtk_widget_set_sensitive(item, bool(action))
            if action:
                gobject.g_signal_connect_data(item, b"activate", clicked, ptr(i), None, 0)
            gtk.gtk_menu_shell_append(menu, item)

        @C.CFUNCTYPE(C.c_int, ptr)
        def refresh(_data):
            nonlocal previous
            current = TrayMenu.entries()
            for i, (action, title) in enumerate(current):
                if previous[i] == current[i]:
                    continue
                actions[i] = action
                gtk.gtk_menu_item_set_label(items[i], title.encode())
            previous = current
            return 1

        glib = C.CDLL("libglib-2.0.so.0")
        glib.g_timeout_add_seconds(1, refresh, None)
        gtk.gtk_widget_show_all(menu)
        indicator.app_indicator_set_menu(instance, menu)
        indicator.app_indicator_set_status(instance, 1)
        ShortcutBackend.ready()
        gtk.gtk_main()


class TrayIcon:
    """Sceglie la variante per il sistema in uso."""

    @staticmethod
    def backend() -> TrayBackend:
        if sys.platform == "darwin":
            return MacTray()
        if sys.platform == "win32":
            return WindowsTray()
        return LinuxTray()

    @classmethod
    def run(cls) -> None:
        cls.backend().run()
