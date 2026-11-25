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
from contextlib import contextmanager
from glob import glob

from .. import private_tmp
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
    with private_tmp.workspace() as tmp:
        return _run_in_workspace(cmd, timeout, stop, tmp)


def _run_in_workspace(cmd, timeout, stop, tmp):
    kwargs: dict = {}
    kwargs["env"] = private_tmp.child_environment(tmp)
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
    with tempfile.TemporaryFile(dir=tmp) as out_f, tempfile.TemporaryFile(dir=tmp) as err_f:
        proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, **kwargs)
        cancelled = getattr(stop, "cancelled", stop.is_set)  # la pausa si applica tra pagine OCR
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if cancelled():
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


@contextmanager
def _render_pages(path, tmp, stop):
    image = str(tmp / "pagina.png")
    if importlib.util.find_spec("pypdfium2"):
        from ..pdf_render import open_for_ocr, page_to_png
        with open_for_ocr(path) as doc:
            def render(n):
                page_to_png(doc, n-1, image)
                return image
            yield render
    elif pdftoppm := which("pdftoppm"):
        def render(n):
            rc, _, err = _run_subprocess(
                [pdftoppm, "-r", "200", "-f", str(n), "-l", str(n), "-singlefile", "-png", path, str(tmp / "pagina")],
                timeout=120, stop=stop)
            if rc:
                raise RuntimeError(err.decode("utf-8", "replace").strip()[:200] or "pdftoppm fallito")
            return image
        yield render
    else:
        try:
            import fitz
        except ImportError:
            raise RuntimeError("OCR dei PDF: serve pypdfium2, pdftoppm (Poppler) oppure PyMuPDF")
        with fitz.open(path) as doc:
            def render(n):
                if n <= len(doc):
                    doc[n-1].get_pixmap(dpi=200).save(image)
                    return image
            yield render


def ocr_pdf_pages(path: str, pages: list[int], cfg: dict,
                  stop: threading.Event | None = None, on_page=None, on_error=None) -> dict[int, str]:
    """Disegna e riconosce una pagina alla volta. I problemi locali a una pagina sono segnalati al chiamante."""
    results = {}
    if not pages:
        return results
    with private_tmp.workspace() as tmp, _render_pages(path, tmp, stop) as render:
        for p in pages:
            if stop is not None and stop.is_set():
                raise Stopped()
            try:
                image = render(p)
                if image is None:
                    continue
                results[p] = _tesseract(image, cfg["ocr_lang"], stop=stop)
            except Stopped:
                raise
            except Exception as exc:
                if on_error is None:
                    raise
                on_error(p, exc)
                continue
            finally:
                (tmp / "pagina.png").unlink(missing_ok=True)
            if on_page:
                on_page(p, results[p])
    return results


def ocr_pdf(path: str, cfg: dict, stop: threading.Event | None = None) -> str:
    """Esegue l'OCR fino a `ocr_max_pages` pagine (compatibilità)."""
    max_pages = max(1, int(cfg.get("ocr_max_pages", 50) or 50))
    pages_map = ocr_pdf_pages(path, list(range(1, max_pages + 1)), cfg, stop=stop)
    return "\n".join(pages_map[p] for p in sorted(pages_map))


class _OcrCache:
    """Pagine già passate all'OCR di un PDF, salvate nel database man mano: se l'app viene
    chiusa a metà di un PDF lungo, alla riapertura si riparte dalla pagina dopo l'ultima fatta."""

    def __init__(self, con: sqlite3.Connection, fp: str, lang: str, path: str):
        self.path = path
        self.con, self.key = con, f"{fp}|{lang}"

    def get(self) -> dict[int, str]:
        return {p: t for p, t in self.con.execute(
            "SELECT page, text FROM ocr_cache WHERE key=?", (self.key,))}

    def put(self, page: int, text: str) -> None:
        self.con.execute("INSERT OR IGNORE INTO origini_cache_ocr VALUES(?,?)", (self.path, self.key))
        self.con.execute("INSERT OR REPLACE INTO ocr_cache(key,page,text) VALUES(?,?,?)",
                         (self.key, page, text))
        self.con.commit()

    def clear(self) -> None:
        self.con.execute("DELETE FROM ocr_cache WHERE key=?", (self.key,))
        self.con.execute("DELETE FROM origini_cache_ocr WHERE key=?", (self.key,))
