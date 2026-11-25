#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Costruisce l'app desktop di CercaDoc SENZA alcun modulo di rete e la verifica.

Va lanciato sul sistema di destinazione (PyInstaller non fa cross-compilazione):
    Windows -> dist/CercaDoc/CercaDoc.exe
    macOS   -> dist/CercaDoc.app
    Linux   -> dist/CercaDoc/CercaDoc

    python -m venv .venv-build
    .venv-build/bin/pip install pyinstaller pypdf customtkinter snowballstemmer sqlcipher3 pypdfium2 Pillow numpy
    .venv-build/bin/python build.py

Lo script si ferma con errore se nel pacchetto finito c'è un modulo di rete
o se l'auto-verifica dell'eseguibile fallisce. Per distribuire basta consegnare
la cartella (o l'.app) / uno zip.
"""
import platform
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cercadoc.offline import NETWORK_MODULES  # noqa: E402  (stessa lista usata a runtime)

SYSTEM = platform.system()  # Windows | Darwin | Linux
# moduli da escludere dal pacchetto (oltre a quelli di rete elencati in cercadoc/offline.py)
EXTRA_EXCLUDES = ["pip", "setuptools", "unittest", "pydoc", "doctest", "distutils", "multiprocessing"]
# nomi di file/moduli che indicano capacità di rete
FORBIDDEN = re.compile(r"(^|[/\\._])(_socket|_ssl|socket|ssl|urllib|http|ftplib|smtplib|asyncio|"
                       r"xmlrpc|webbrowser)([._/\\]|$)", re.I)


def executable(dist: Path) -> Path:
    if SYSTEM == "Darwin":
        return dist / "CercaDoc.app" / "Contents" / "MacOS" / "CercaDoc"
    return dist / "CercaDoc" / ("CercaDoc.exe" if SYSTEM == "Windows" else "CercaDoc")


def main() -> int:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
           "--name", "CercaDoc", "--hidden-import", "pypdf", "--hidden-import", "cercadoc.gui",
           "--paths", str(HERE),
           "--collect-data", "customtkinter", "--collect-all", "pypdfium2_raw", "--collect-data", "pypdfium2",
           "--collect-all", "sqlcipher3", "--hidden-import", "PIL.ImageTk",
           "--copy-metadata", "sqlcipher3", "--copy-metadata", "pypdfium2", "--copy-metadata", "Pillow"]
    for name in ("LICENSE", "THIRD_PARTY_NOTICES"):
        cmd += ["--add-data", str(HERE / name) + ":."]
    if importlib.util.find_spec("onnxruntime") and importlib.util.find_spec("sentencepiece"):
        cmd += ["--collect-binaries", "onnxruntime", "--collect-data", "onnxruntime", "--hidden-import", "sentencepiece"]
    for m in sorted(set(NETWORK_MODULES) | set(EXTRA_EXCLUDES)):
        if "." not in m:  # escludere il pacchetto radice esclude anche i sotto-moduli
            cmd += ["--exclude-module", m]
    cmd.append(str(HERE / "cercadoc" / "__main__.py"))
    print(">>", " ".join(cmd))
    if subprocess.call(cmd, cwd=HERE) != 0:
        return 1

    dist = HERE / "dist"
    exe = executable(dist)

    # 0) toglie da Tcl/Tk le librerie "http" (script Tcl che l'interfaccia non usa)
    removed = False
    for p in list(dist.rglob("http1.0")) + list(dist.rglob("http-*.tm")):
        shutil.rmtree(p) if p.is_dir() else p.unlink()
        print("rimosso dal pacchetto:", p.relative_to(dist))
        removed = True
    if removed and SYSTEM == "Darwin":  # la modifica invalida la firma: si rifà (firma locale)
        subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(dist / "CercaDoc.app")], check=False)

    # 1) nessun file di rete tra quelli distribuiti (librerie binarie di estensione)
    bad = [str(p.relative_to(dist)) for p in dist.rglob("*") if p.is_file() and FORBIDDEN.search(p.name)]
    # 2) nessun modulo di rete nell'archivio del codice Python (PYZ)
    from PyInstaller.archive.readers import ZlibArchiveReader
    pyz = HERE / "build" / "CercaDoc" / "PYZ-00.pyz"
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
    print(f"OK: {exe.parent if SYSTEM != 'Darwin' else dist / 'CercaDoc.app'} non contiene né può usare moduli di rete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
