# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""OCR con Tesseract (e Poppler/PyMuPDF per disegnare le pagine dei PDF)."""
from __future__ import annotations

import os
import importlib.util
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from glob import glob

from ..errors import Stopped


def which(name: str) -> str | None:
    """Come shutil.which, ma cerca anche nei percorsi tipici di Homebrew (macOS) e Windows."""
    found = shutil.which(name)
    if found:
        return found
    extras: list[str] = []
    if sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            if not base:
                continue
            extras.append(os.path.join(base, "Tesseract-OCR"))
            extras.append(os.path.join(base, "poppler", "Library", "bin"))
            extras.extend(glob(os.path.join(base, "poppler*", "Library", "bin")))
            extras.extend(glob(os.path.join(base, "poppler*", "bin")))
        extras.extend([r"C:\poppler\Library\bin", r"C:\poppler\bin"])
    else:
        extras.extend(["/opt/homebrew/bin", "/usr/local/bin"])
    extra_path = os.pathsep.join(p for p in extras if p and os.path.isdir(p))
    return shutil.which(name, path=extra_path) if extra_path else None


def ocr_available() -> bool:
    return which("tesseract") is not None


def _run_subprocess(cmd: list[str], timeout: float,
                    stop: threading.Event | None = None) -> tuple[int, bytes, bytes]:
    """Esegue un comando esterno (OCR) in secondo piano, con un solo thread di calcolo,
    interrompendolo subito se `stop` viene attivato."""
    kwargs: dict = {}
    env = dict(os.environ, OMP_THREAD_LIMIT="1")  # Tesseract: un solo thread
    kwargs["env"] = env
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                   | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    else:
        nice = shutil.which("nice")
        if nice:
            cmd = [nice, "-n", "10"] + cmd
    if stop is None:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **kwargs)
        return r.returncode, r.stdout, r.stderr
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, **kwargs)
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if stop.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2)
                    raise Stopped()
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.wait(timeout=2)
                    raise subprocess.TimeoutExpired(cmd, timeout)
                time.sleep(0.1)
        except BaseException:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
            raise
        out_f.seek(0)
        err_f.seek(0)
        return proc.returncode, out_f.read(), err_f.read()


def _tesseract(image: str, lang: str, stop: threading.Event | None = None) -> str:
    exe = which("tesseract")
    if not exe:
        raise RuntimeError("Tesseract non installato")
    rc, out, err = _run_subprocess([exe, image, "stdout", "-l", lang], timeout=300, stop=stop)
    if rc != 0:
        raise RuntimeError(err.decode("utf-8", "replace").strip()[:200] or f"Tesseract errore ({rc})")
    return out.decode("utf-8", "replace")


def ocr_pdf_pages(path: str, pages: list[int], cfg: dict,
                  stop: threading.Event | None = None, on_page=None) -> dict[int, str]:
    """Esegue l'OCR solo sulle pagine richieste (numerate da 1), interrompibile.
    `on_page(pagina, testo)` viene chiamata appena una pagina è pronta (per salvarla subito)."""
    if not pages:
        return {}
    tmp = tempfile.mkdtemp(prefix="cercadoc_")
    results: dict[int, str] = {}
    try:
        if importlib.util.find_spec("pypdfium2"):
            from ..pdf_render import open_for_ocr, page_to_png
            img = os.path.join(tmp, "pagina.png")
            with open_for_ocr(path) as doc:
                for p in pages:
                    if stop is not None and stop.is_set():
                        raise Stopped()
                    page_to_png(doc, p-1, img)
                    results[p] = _tesseract(img, cfg["ocr_lang"], stop=stop)
                    if on_page:
                        on_page(p, results[p])
                    os.remove(img)
        elif pdftoppm := which("pdftoppm"):
            for p in pages:
                if stop is not None and stop.is_set():
                    raise Stopped()
                prefix = os.path.join(tmp, f"p{p:04d}")
                rc, _, err = _run_subprocess(
                    [pdftoppm, "-r", "200", "-f", str(p), "-l", str(p), "-png", path, prefix],
                    timeout=120, stop=stop)
                if rc != 0:
                    raise RuntimeError(err.decode("utf-8", "replace").strip()[:200] or "pdftoppm fallito")
                imgs = sorted(glob(prefix + "*.png"))
                if imgs:
                    results[p] = _tesseract(imgs[0], cfg["ocr_lang"], stop=stop)
                    if on_page:
                        on_page(p, results[p])
                    try:
                        os.remove(imgs[0])
                    except OSError:
                        pass
        else:
            try:
                import fitz  # PyMuPDF
            except ImportError:
                raise RuntimeError("OCR dei PDF: serve pypdfium2, pdftoppm (Poppler) oppure PyMuPDF")
            with fitz.open(path) as doc:
                for p in pages:
                    if stop is not None and stop.is_set():
                        raise Stopped()
                    if p - 1 >= len(doc):
                        continue
                    img = os.path.join(tmp, f"p{p:04d}.png")
                    doc[p - 1].get_pixmap(dpi=200).save(img)
                    results[p] = _tesseract(img, cfg["ocr_lang"], stop=stop)
                    if on_page:
                        on_page(p, results[p])
                    try:
                        os.remove(img)
                    except OSError:
                        pass
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ocr_pdf(path: str, cfg: dict, stop: threading.Event | None = None) -> str:
    """Esegue l'OCR fino a `ocr_max_pages` pagine (compatibilità)."""
    max_pages = max(1, int(cfg.get("ocr_max_pages", 50) or 50))
    pages_map = ocr_pdf_pages(path, list(range(1, max_pages + 1)), cfg, stop=stop)
    return "\n".join(pages_map[p] for p in sorted(pages_map))


class _OcrCache:
    """Pagine già passate all'OCR di un PDF, salvate nel database man mano: se l'app viene
    chiusa a metà di un PDF lungo, alla riapertura si riparte dalla pagina dopo l'ultima fatta."""

    def __init__(self, con: sqlite3.Connection, fp: str, lang: str):
        self.con, self.key = con, f"{fp}|{lang}"

    def get(self) -> dict[int, str]:
        return {p: t for p, t in self.con.execute(
            "SELECT page, text FROM ocr_cache WHERE key=?", (self.key,))}

    def put(self, page: int, text: str) -> None:
        self.con.execute("INSERT OR REPLACE INTO ocr_cache(key,page,text) VALUES(?,?,?)",
                         (self.key, page, text))
        self.con.commit()

    def clear(self) -> None:
        self.con.execute("DELETE FROM ocr_cache WHERE key=?", (self.key,))
