# SPDX-License-Identifier: GPL-3.0-or-later
"""PDFium con accesso serializzato tra indicizzazione e anteprima."""
import math
import threading
from contextlib import closing, contextmanager
from functools import wraps

_lock = threading.RLock()


def serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapped


def scale_for(page, scale):
    width, height = page.get_size()
    if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
        raise ValueError("Dimensioni della pagina PDF non valide.")
    return min(scale, math.sqrt(16_000_000 / (width * height)), 16000 / max(width, height))


@contextmanager
def open_for_ocr(source):
    import pypdfium2 as pdf
    with _lock:
        doc = pdf.PdfDocument(source)
    try:
        with _lock:
            doc.init_forms()
        yield doc
    finally:
        with _lock:
            doc.close()


@serialized
def page_to_png(doc, index, dest):
    with closing(doc[index]) as page, closing(page.render(scale=scale_for(page, 200/72))) as bitmap:
        bitmap.to_pil().save(dest, "PNG")
