# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Estrattori per i formati Office/ODF (docx, pptx, xlsx, odt/ods/odp)."""
from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

from stillfind.extractors.base import ExtractionRequest, Extractor


class OfficePackage(zipfile.ZipFile):
    """Un pacchetto ZIP di Office o OpenDocument, letto con un limite alla dimensione decompressa."""

    MAX_XML_BYTES = 50 * 1024 * 1024  # limite decompressione XML interno (anti ZIP-bomb)

    @staticmethod
    def local(tag: str) -> str:
        """Nome dell'elemento senza lo spazio dei nomi."""
        return tag.rsplit("}", 1)[-1]

    def read_limited(self, name: str, max_bytes: int | None = None) -> bytes:
        """Legge un membro con limite di dimensione decompressa."""
        max_bytes = self.MAX_XML_BYTES if max_bytes is None else max_bytes
        with self.open(name) as f:
            data = f.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise RuntimeError(f"Contenuto interno troppo grande ({name})")
        return data

    def paragraph_texts(self, name: str, para: str, text: str) -> list[str]:
        """Testo di ogni paragrafo (tag locali `para`) di un membro XML, concatenando i tag `text`."""
        root = ET.fromstring(self.read_limited(name))
        out = []
        control = {"br": "\n", "cr": "\n", "tab": "\t"}
        for p in root.iter():
            if self.local(p.tag) == para:
                line = "".join((t.text or "") if self.local(t.tag) == text else control.get(self.local(t.tag), "")
                               for t in p.iter())
                if line.strip():
                    out.append(line)
        return out

    def relationships(self, source: str, members: set[str]) -> dict[str, str]:
        """Collegamenti `Id -> membro` descritti nel file `_rels` di un membro."""
        parent, name = posixpath.split(source)
        rels = posixpath.join(parent, "_rels", name + ".rels")
        if rels not in members:
            return {}
        out = {}
        for rel in ET.fromstring(self.read_limited(rels)):
            if rel.get("TargetMode") == "External":
                continue
            target = posixpath.normpath(posixpath.join(parent, rel.get("Target", ""))).lstrip("/")
            if target in members:
                out[rel.get("Id")] = target
        return out


def _sections(blocks, info, kind) -> str:
    """Unisce i blocchi (titolo, testo) e registra in `info` dove comincia ciascuno."""
    starts, parts, pos = [], [], 0
    for title, text in blocks:
        starts.append((pos, kind, title, ""))
        parts.append(text)
        pos += len(text) + 2
    if info is not None:
        info["sections"] = starts
    return "\n\n".join(parts)


class DocxExtractor(Extractor):
    extensions = ("docx",)

    def extract(self, request: ExtractionRequest) -> tuple[str, bool]:
        out = []
        with OfficePackage(request.path) as z:
            names = [n for n in z.namelist() if re.fullmatch(
                r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml", n)]
            for n in sorted(names):
                out += z.paragraph_texts(n, "p", "t")
        return "\n\n".join(out), False


class PptxExtractor(Extractor):
    extensions = ("pptx",)

    def extract(self, request: ExtractionRequest) -> tuple[str, bool]:
        blocks = []
        with OfficePackage(request.path) as z:
            members = set(z.namelist())
            main = "ppt/presentation.xml"
            rels = z.relationships(main, members)
            slides = []
            if main in members:
                for el in ET.fromstring(z.read_limited(main)).iter():
                    if z.local(el.tag) == "sldId":
                        rid = next((v for k, v in el.attrib.items() if k.endswith("}id")), None)
                        if rid in rels:
                            slides.append(rels[rid])
            if not slides:
                slides = sorted((s for s in members if re.fullmatch(r"ppt/slides/slide\d+\.xml", s)),
                                key=lambda s: int(re.search(r"(\d+)\.xml$", s)[1]))
            for number, slide in enumerate(slides, 1):
                paragraphs = z.paragraph_texts(slide, "p", "t")
                notes = next((n for n in z.relationships(slide, members).values()
                              if n.startswith("ppt/notesSlides/")), None)
                if notes:
                    paragraphs.extend(z.paragraph_texts(notes, "p", "t"))
                blocks.append((str(number), "\n\n".join(paragraphs)))
        return _sections(blocks, request.info, "slide"), False


class XlsxExtractor(Extractor):
    """Testo, numeri, valori calcolati e nomi dei fogli, preservando le righe."""

    extensions = ("xlsx",)

    @staticmethod
    def _sheet_names(z: OfficePackage, names_set: set[str]) -> dict[str, str]:
        """Mappa 'xl/worksheets/sheet1.xml' -> nome del foglio, se disponibile."""
        if "xl/workbook.xml" not in names_set or "xl/_rels/workbook.xml.rels" not in names_set:
            return {}
        try:
            rid_to_target = z.relationships("xl/workbook.xml", names_set)
            wb_root = ET.fromstring(z.read_limited("xl/workbook.xml"))
            out: dict[str, str] = {}
            for el in wb_root.iter():
                if z.local(el.tag) == "sheet":
                    sname = el.attrib.get("name")
                    rid = next((v for k, v in el.attrib.items() if z.local(k) == "id"), None)
                    if sname and rid and rid in rid_to_target:
                        out[rid_to_target[rid]] = sname
            return out
        except Exception:
            return {}

    @staticmethod
    def _cell_value(c, shared: list[str], local) -> str:
        ctype = c.attrib.get("t", "")
        if ctype == "e":
            return ""  # errori formula (#REF!, #DIV/0!, ecc.)
        val = ""
        if ctype == "inlineStr":
            val = "".join(t.text or "" for t in c.iter() if local(t.tag) == "t")
        else:
            v_el = next((ch for ch in c if local(ch.tag) == "v"), None)
            if v_el is not None and v_el.text is not None:
                raw_v = v_el.text
                if ctype == "s":
                    try:
                        val = shared[int(raw_v)]
                    except (ValueError, IndexError):
                        val = ""
                elif ctype == "b":
                    val = "VERO" if raw_v == "1" else "FALSO"
                else:
                    val = raw_v
        return val.strip()

    def extract(self, request: ExtractionRequest) -> tuple[str, bool]:
        blocks = []
        with OfficePackage(request.path) as z:
            local = z.local
            names = z.namelist()
            names_set = set(names)
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names_set:
                sroot = ET.fromstring(z.read_limited("xl/sharedStrings.xml"))
                for el in sroot.iter():
                    if local(el.tag) == "si":
                        shared.append("".join(t.text or "" for t in el.iter() if local(t.tag) == "t"))
            sheet_map = self._sheet_names(z, names_set)
            sheets = [n for n in names if re.fullmatch(r"xl/worksheets/[^/]+\.xml", n)]
            sheets.sort(key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)])
            sheets = list(dict.fromkeys([*(n for n in sheet_map if n.startswith("xl/worksheets/")), *sheets]))
            for number, n in enumerate(sheets, 1):
                sheet_title = sheet_map.get(n)
                sheet_lines: list[str] = []
                root = ET.fromstring(z.read_limited(n))
                for row in root.iter():
                    if local(row.tag) != "row":
                        continue
                    cells = [v for c in row if local(c.tag) == "c" and (v := self._cell_value(c, shared, local))]
                    if cells:
                        sheet_lines.append(" | ".join(cells))
                if sheet_lines:
                    if sheet_title:
                        sheet_lines.insert(0, f"[{sheet_title}]")
                    blocks.append((sheet_title or str(number), "\n".join(sheet_lines)))
            if not blocks and shared:
                return "\n".join(s for s in shared if s.strip()), False
        return _sections(blocks, request.info, "sheet"), False


class OdfExtractor(Extractor):
    extensions = ("odt", "ods", "odp")

    def extract(self, request: ExtractionRequest) -> tuple[str, bool]:
        with OfficePackage(request.path) as z:
            root = ET.fromstring(z.read_limited("content.xml"))
        out = []
        for el in root.iter():
            if OfficePackage.local(el.tag) in ("p", "h"):
                line = "".join(el.itertext())
                if line.strip():
                    out.append(line)
        separator = "\n\n" if str(request.path).lower().endswith(".odt") else "\n"
        return separator.join(out), False
