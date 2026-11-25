# SPDX-License-Identifier: GPL-3.0-or-later
"""Icona della sessione utente. Il clic usa lo stesso comando della scorciatoia."""
import ctypes as C
import os
import sys

from .hotkey import _pressed, _ready


def remove_icon():
    pass


def mac():
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
    @C.CFUNCTYPE(None, ptr, ptr, ptr)
    def clicked(_self, _cmd, _sender):
        _pressed()
    action = objc.sel_registerName(b"openSearch:")
    handler = objc.objc_allocateClassPair(cls("NSObject"), b"CercaDocTrayAction", 0)
    objc.class_addMethod(handler, action, clicked, b"v@:@")
    objc.objc_registerClassPair(handler)
    target = send(send(handler, "alloc"), "init")
    bar = send(cls("NSStatusBar"), "systemStatusBar")
    item = send(bar, "statusItemWithLength:", ptr, [C.c_double], [-1.0])
    button = send(item, "button")
    send(button, "setTitle:", None, [ptr], [ns("⌕")])
    send(button, "setToolTip:", None, [ptr], [ns("CercaDoc · Apri ricerca")])
    send(button, "setTarget:", None, [ptr], [target])
    send(button, "setAction:", None, [ptr], [action])
    _ready()
    send(app, "run", None)
    send(pool, "drain", None)


def windows():
    global remove_icon
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
    @callback
    def receive(hwnd, event, wparam, lparam):
        if event == msg_tray and lparam == 0x202:  # WM_LBUTTONUP
            _pressed()
            return 0
        if event == restart:
            shell.Shell_NotifyIconW(0, C.byref(icon))
            return 0
        return user.DefWindowProcW(hwnd, event, wparam, lparam)
    kernel.GetModuleHandleW.restype = W.HMODULE
    instance = kernel.GetModuleHandleW(None)
    wc = WindowClass()
    wc.proc, wc.instance, wc.name = receive, instance, "CercaDocTray"
    if not user.RegisterClassW(C.byref(wc)):
        raise C.WinError()
    user.CreateWindowExW.restype = W.HWND
    user.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int,
                                     C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p]
    hwnd = user.CreateWindowExW(0, wc.name, "", 0, 0, 0, 0, 0, None, None, instance, None)
    if not hwnd:
        raise C.WinError()
    user.LoadIconW.restype = W.HICON
    icon = Icon(C.sizeof(Icon), hwnd, 1, 7, msg_tray, user.LoadIconW(None, C.c_void_p(32512)), "CercaDoc · Apri ricerca")
    if not shell.Shell_NotifyIconW(0, C.byref(icon)):
        raise C.WinError()
    remove_icon = lambda: shell.Shell_NotifyIconW(2, C.byref(icon))
    _ready()
    msg = W.MSG()
    try:
        while user.GetMessageW(C.byref(msg), None, 0, 0) > 0:
            user.TranslateMessage(C.byref(msg))
            user.DispatchMessageW(C.byref(msg))
    finally:
        remove_icon()


def linux():
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
    instance = ptr(indicator.app_indicator_new(b"cercadoc", b"system-search", 0))
    gtk.gtk_menu_new.restype = gtk.gtk_menu_item_new_with_label.restype = ptr
    menu = ptr(gtk.gtk_menu_new())
    item = ptr(gtk.gtk_menu_item_new_with_label("Apri ricerca…".encode()))
    @C.CFUNCTYPE(None, ptr, ptr)
    def activate(_item, _data):
        _pressed()
    gobject.g_signal_connect_data(item, b"activate", activate, None, None, 0)
    gtk.gtk_menu_shell_append(menu, item)
    gtk.gtk_widget_show_all(menu)
    indicator.app_indicator_set_menu(instance, menu)
    indicator.app_indicator_set_status(instance, 1)
    _ready()
    gtk.gtk_main()


def run():
    if sys.platform == "darwin":
        mac()
    elif sys.platform == "win32":
        windows()
    else:
        linux()
