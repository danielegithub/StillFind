# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Oscuramento sul contenuto originale: le sole immagini approvate entrano nel nuovo PDF."""
import csv
import io
import os
import re
import subprocess
from contextlib import closing
from pathlib import Path

from stillfind.core import facts
from stillfind.core.private_tmp import PrivateWorkspace
from stillfind.extractors.ocr import ExternalTool
from stillfind.extractors.pdf_render import scale_for, serialized
from stillfind.extractors.text import TextDecoder


class PdfReview:
    @serialized
    def __init__(self, source):
        import pypdfium2 as pdf
        self.source = pdf.PdfDocument(source)
        self.pages = len(self.source)
        self.source.init_forms()
        self.output = pdf.PdfDocument.new()
        self.done = 0
        self.marks = 0

    @serialized
    def prepare(self, types, terms, lang):
        with closing(self.source[self.done]) as page:
            width, height = page.get_size()
            scale = scale_for(page, 3.0)
            bitmap = page.render(scale=scale, draw_annots=True)
            try:
                image = bitmap.to_pil().convert("RGB")
                convert = bitmap.get_posconv(page)
                boxes = []
                with closing(page.get_textpage()) as tp:
                    # PDFium può saltare o espandere caratteri. La mappa esplicita mantiene le coordinate.
                    chars, indices = [], []
                    for i in range(tp.count_chars()):
                        part = tp.get_text_range(i, 1)
                        chars.append(part)
                        indices.extend([i] * len(part))
                    text = "".join(chars)
                    words = re.compile(r"\S+")
                    for start, end in Redactor.spans(text, types, terms):
                        for word in words.finditer(text, start, end):
                            points = []
                            for i in set(indices[word.start():word.end()]):
                                left, bottom, right, top = tp.get_charbox(i)
                                if right > left and top > bottom:
                                    points.extend((convert.to_bitmap(left,bottom), convert.to_bitmap(right,top)))
                            if points:
                                xs, ys = zip(*points)
                                boxes.append((min(xs), min(ys), max(xs), max(ys)))
                if lang and (types or any(t.strip() for t in terms)):
                    boxes.extend(Redactor._ocr_rects(image, lang, types, terms))
                Redactor._mark(image, boxes)
                return image, (width,height), len(boxes)
            finally:
                bitmap.close()

    @serialized
    def approve(self, image, size, manual, detected):
        import pypdfium2 as pdf
        Redactor._mark(image, manual)
        jpg = io.BytesIO()
        image.save(jpg, "JPEG", quality=92, subsampling=0)
        jpg.seek(0)
        page = self.output.new_page(*size)
        try:
            obj = pdf.PdfImage.new(self.output)
            obj.load_jpeg(jpg, inline=True)
            obj.set_matrix(pdf.PdfMatrix().scale(*size))
            page.insert_obj(obj)
            page.gen_content()
        finally:
            page.close()
        self.done += 1
        self.marks += detected + len(manual)

    @serialized
    def save(self, dest):
        if not self.done or self.done != self.pages or not self.marks:
            raise ValueError("Controlla tutte le pagine e seleziona almeno una zona da oscurare.")
        Redactor._new_copy(dest, self.output.save)

    @serialized
    def close(self):
        self.output.close()
        self.source.close()


class Redactor:
    """Copie oscurate di documenti di testo e immagini."""

    @staticmethod
    def spans(text, types, terms):
        types = set(types)
        selected = {term.strip().upper().replace(" ", "") for term in terms if term.strip()}
        checked = types | ({"iban", "cf", "piva"} if selected else set())
        matches = [(a, b) for kind, value, _, _, a, b in facts.entities(text, checked, None)
                   if kind in types or value in selected]
        for term in terms:
            words = term.strip().split()
            if words:
                pattern = r"\s+".join(re.escape(w) for w in words)
                matches.extend(m.span() for m in re.finditer(pattern, text, re.IGNORECASE))
        merged = []
        for a, b in sorted(matches):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        return merged

    @staticmethod
    def _new_copy(path, writer):
        output = Path(path)
        with output.open("xb") as f:
            try:
                writer(f)
                f.flush()
                os.fsync(f.fileno())
            except BaseException:
                f.close()
                output.unlink(missing_ok=True)
                raise

    @staticmethod
    def redact_text(source, dest, types, terms):
        text = TextDecoder.decode(Path(source).read_bytes())
        found = Redactor.spans(text, types, terms)
        if not found:
            raise ValueError("Nessuna occorrenza da oscurare.")
        def write(f):
            pos = 0
            for a, b in found:
                f.write(text[pos:a].encode("utf-8"))
                f.write(b"[OMISSIS]")
                pos = b
            f.write(text[pos:].encode("utf-8"))
        Redactor._new_copy(dest, write)
        return len(found)

    @staticmethod
    def _mark(image, rects, padding=2):
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        for x1, y1, x2, y2 in rects:
            draw.rectangle((min(x1,x2)-padding, min(y1,y2)-padding,
                            max(x1,x2)+padding, max(y1,y2)+padding), fill="black")

    @staticmethod
    def _ocr_rects(image, lang, types, terms):
        exe = ExternalTool.which("tesseract")
        if not exe:
            raise RuntimeError("Per controllare anche il testo nelle immagini serve Tesseract.")
        data = io.BytesIO()
        image.save(data, format="PNG")
        with PrivateWorkspace.workspace() as tmp:
            run = subprocess.run([exe, "stdin", "stdout", "-l", lang, "tsv"], input=data.getvalue(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
                                 env=PrivateWorkspace.child_environment(tmp))
        if run.returncode:
            raise RuntimeError("OCR non riuscito. Verifica la lingua installata nelle Impostazioni.")
        words, boxes, offset = [], [], 0
        for row in csv.DictReader(io.StringIO(run.stdout.decode("utf-8", "replace")), delimiter="\t",
                                  quoting=csv.QUOTE_NONE):
            word = (row.get("text") or "").strip()
            if row["level"] != "5" or not word:
                continue
            x,y,w,h = (int(row[k]) for k in ("left","top","width","height"))
            words.append(word)
            boxes.append((offset, offset+len(word), (x,y,x+w,y+h)))
            offset += len(word)+1
        matches = Redactor.spans(" ".join(words), types, terms)
        j = 0
        for a, b, rect in boxes:
            while j < len(matches) and matches[j][1] <= a:
                j += 1
            if j < len(matches) and matches[j][0] < b:
                yield rect
