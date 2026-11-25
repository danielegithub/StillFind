# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""OCR con Tesseract (e Poppler/PyMuPDF per disegnare le pagine dei PDF)."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from glob import glob

from stillfind.core.private_tmp import PrivateWorkspace
from stillfind.core.errors import Stopped
from stillfind.extractors.base import ExtractionRequest, Extractor


class ExternalTool:
    """Ricerca e avvio dei programmi esterni (Tesseract, Poppler)."""

    @staticmethod
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

    @classmethod
    def run(cls, cmd: list[str], timeout: float,
            stop: threading.Event | None = None) -> tuple[int, bytes, bytes]:
        """Esegue un comando esterno (OCR) in secondo piano, con un solo thread di calcolo,
        interrompendolo subito se `stop` viene attivato."""
        with PrivateWorkspace.workspace() as tmp:
            return cls._run_in_workspace(cmd, timeout, stop, tmp)

    @staticmethod
    def _run_in_workspace(cmd, timeout, stop, tmp):
        kwargs: dict = {}
        kwargs["env"] = PrivateWorkspace.child_environment(tmp)
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


class Tesseract:
    """Riconoscimento del testo di immagini e di pagine PDF in una lingua."""

    def __init__(self, lang: str):
        self.lang = lang

    @staticmethod
    def available() -> bool:
        return ExternalTool.which("tesseract") is not None

    def recognize(self, image: str, stop: threading.Event | None = None) -> str:
        exe = ExternalTool.which("tesseract")
        if not exe:
            raise RuntimeError("Tesseract non installato")
        rc, out, err = ExternalTool.run([exe, image, "stdout", "-l", self.lang], timeout=300, stop=stop)
        if rc != 0:
            raise RuntimeError(err.decode("utf-8", "replace").strip()[:200] or f"Tesseract errore ({rc})")
        return out.decode("utf-8", "replace")

    @staticmethod
    @contextmanager
    def _render_pages(path, tmp, stop):
        """Fornisce `render(n)`: disegna la pagina `n` del PDF e ne restituisce il file immagine."""
        image = str(tmp / "pagina.png")
        if importlib.util.find_spec("pypdfium2"):
            from stillfind.extractors.pdf_render import open_for_ocr, page_to_png
            with open_for_ocr(path) as doc:
                def render(n):
                    page_to_png(doc, n-1, image)
                    return image
                yield render
        elif pdftoppm := ExternalTool.which("pdftoppm"):
            def render(n):
                rc, _, err = ExternalTool.run(
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

    def recognize_pdf_pages(self, path: str, pages: list[int], stop: threading.Event | None = None,
                            on_page=None, on_error=None) -> dict[int, str]:
        """Disegna e riconosce una pagina alla volta. I problemi locali a una pagina sono segnalati al chiamante."""
        results = {}
        if not pages:
            return results
        with PrivateWorkspace.workspace() as tmp, self._render_pages(path, tmp, stop) as render:
            for p in pages:
                if stop is not None and stop.is_set():
                    raise Stopped()
                try:
                    image = render(p)
                    if image is None:
                        continue
                    results[p] = self.recognize(image, stop=stop)
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


class ImageExtractor(Extractor):
    """Immagini: solo con OCR."""

    extensions = ("png", "jpg", "jpeg", "tif", "tiff", "bmp")

    def extract(self, request: ExtractionRequest) -> tuple[str, bool]:
        if not Tesseract.available():
            raise RuntimeError("Tesseract non installato")
        if request.cfg.get("_ocr_started"):
            request.cfg["_ocr_started"]()
        return Tesseract(request.cfg["ocr_lang"]).recognize(request.path, stop=request.stop), True


class OcrCache:
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
