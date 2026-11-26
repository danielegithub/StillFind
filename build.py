#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Costruisce l'app desktop di StillFind SENZA alcun modulo di rete e la verifica.

Va lanciato sul sistema di destinazione (PyInstaller non fa cross-compilazione):
    Windows -> dist/StillFind/StillFind.exe
    macOS   -> dist/StillFind.app
    Linux   -> dist/StillFind/StillFind

    python -m venv .venv-build
    .venv-build/bin/pip install -r requirements-build.txt
    .venv-build/bin/python packaging/prepare_assets.py
    .venv-build/bin/python build.py

Tesseract deve essere installato sulla macchina di build; viene incorporato nel pacchetto.

Lo script si ferma con errore se nel pacchetto finito c'è un modulo di rete
o se l'auto-verifica dell'eseguibile fallisce. Per distribuire basta consegnare
la cartella (o l'.app) / uno zip.
"""
import importlib.util
import platform
import re
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Legge la stessa lista senza avviare il blocco di rete dell'app nel processo di build.
NETWORK_MODULES = runpy.run_path(str(HERE / "stillfind/core/offline.py"))["NETWORK_MODULES"]

SYSTEM = platform.system()  # Windows | Darwin | Linux
# moduli da escludere dal pacchetto (oltre a quelli di rete elencati in stillfind/core/offline.py)
EXTRA_EXCLUDES = ["pip", "setuptools", "unittest", "pydoc", "doctest", "distutils", "multiprocessing"]
# nomi di file/moduli che indicano capacità di rete
FORBIDDEN = re.compile(r"(^|[/\\._])(_socket|_ssl|socket|ssl|urllib[./\\](?:request|response|error|robotparser)|http|ftplib|smtplib|asyncio|"
                       r"xmlrpc|webbrowser)([._/\\]|$)", re.I)


def executable(dist: Path) -> Path:
    if SYSTEM == "Darwin":
        return dist / "StillFind.app" / "Contents" / "MacOS" / "StillFind"
    return dist / "StillFind" / ("StillFind.exe" if SYSTEM == "Windows" else "StillFind")


def main() -> int:
    sys.path.insert(0, str(HERE / "packaging"))
    from bundle import arguments, verify
    required = ("onnxruntime", "sentencepiece", "sqlcipher3", "pypdfium2", "numpy", "PIL", "tkinter")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError("Build completa: dipendenze mancanti: " + ", ".join(missing)
                           + ". Installa requirements-build.txt.")
    bundle_arguments = arguments()
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
           "--name", "StillFind", "--hidden-import", "pypdf", "--hidden-import", "stillfind.ui.app",
           "--paths", str(HERE),
           "--collect-data", "customtkinter", "--collect-all", "pypdfium2_raw", "--collect-data", "pypdfium2",
           "--collect-all", "sqlcipher3", "--hidden-import", "PIL.ImageTk",
           "--copy-metadata", "sqlcipher3", "--copy-metadata", "pypdfium2", "--copy-metadata", "Pillow"]
    for name in ("LICENSE", "THIRD_PARTY_NOTICES"):
        cmd += ["--add-data", str(HERE / name) + ":."]
    cmd += ["--collect-binaries", "onnxruntime", "--collect-data", "onnxruntime", "--hidden-import", "sentencepiece"]
    cmd += bundle_arguments
    for m in sorted(set(NETWORK_MODULES) | set(EXTRA_EXCLUDES)):
        cmd += ["--exclude-module", m]
    cmd.append(str(HERE / "stillfind" / "__main__.py"))
    print(">> PyInstaller: build completa con modelli e OCR incorporati", flush=True)
    if subprocess.call(cmd, cwd=HERE) != 0:
        return 1

    dist = HERE / "dist"
    exe = executable(dist)
    packages = [p for p in (dist / "StillFind", dist / "StillFind.app") if p.is_dir()]

    # 0) toglie da Tcl/Tk le librerie "http" (script Tcl che l'interfaccia non usa)
    removed = False
    for package in packages:
        for p in [*package.rglob("http1.0"), *package.rglob("http-*.tm")]:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            print("rimosso dal pacchetto:", p.relative_to(dist))
            removed = True
    if removed and SYSTEM == "Darwin":  # la modifica invalida la firma: si rifà (firma locale)
        subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(dist / "StillFind.app")], check=True)

    # 1) nessun file di rete tra quelli distribuiti (librerie binarie di estensione)
    bad = [str(p.relative_to(dist)) for package in packages for p in package.rglob("*")
           if p.is_file() and FORBIDDEN.search(p.name)]
    # 2) nessun modulo di rete nell'archivio del codice Python (PYZ)
    from PyInstaller.archive.readers import ZlibArchiveReader
    pyz = HERE / "build" / "StillFind" / "PYZ-00.pyz"
    if not pyz.exists():
        print("ERRORE: archivio PYZ non trovato, impossibile verificare:", pyz)
        return 2
    modules = list(ZlibArchiveReader(str(pyz)).toc)
    print(f"Moduli Python inclusi: {len(modules)}")
    bad += [m for m in modules if FORBIDDEN.search(m)]
    if bad:
        print("\nERRORE: nel pacchetto ci sono moduli di rete:", *sorted(set(bad)), sep="\n  - ")
        return 2

    # 3) auto-verifica dell'eseguibile reale (esito nel codice di uscita:
    #    funziona anche se l'app a finestra non ha una console)
    print("\nAuto-verifica dell'eseguibile…")
    r = subprocess.run([str(exe), "--self-test-offline"], capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print("ERRORE: l'eseguibile non ha superato la verifica offline", r.stderr)
        return 3
    verify(dist / ("StillFind.app" if SYSTEM == "Darwin" else "StillFind"))
    print(f"OK: {exe.parent if SYSTEM != 'Darwin' else dist / 'StillFind.app'} non contiene né può usare moduli di rete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
