# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Estrattori per i formati Office/ODF (docx, pptx, xlsx, odt/ods/odp)."""
from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

MAX_ZIP_XML_BYTES = 50 * 1024 * 1024  # limite decompressione XML interno (anti ZIP-bomb)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_zip_member(z: zipfile.ZipFile, name: str, max_bytes: int = MAX_ZIP_XML_BYTES) -> bytes:
    """Legge un membro di un archivio ZIP con limite di dimensione decompressa."""
    with z.open(name) as f:
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RuntimeError(f"Contenuto interno troppo grande ({name})")
    return data


def _paragraph_texts(xml_bytes: bytes, para: str, text: str) -> list[str]:
    """Testo di ogni paragrafo (tag locali `para`), concatenando i tag `text`."""
    root = ET.fromstring(xml_bytes)
    out = []
    control = {"br": "\n", "cr": "\n", "tab": "\t"}
    for p in root.iter():
        if _local(p.tag) == para:
            line = "".join((t.text or "") if _local(t.tag) == text else control.get(_local(t.tag), "")
                           for t in p.iter())
            if line.strip():
                out.append(line)
    return out


def extract_docx(path: str) -> str:
    out = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.fullmatch(
            r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml", n)]
        for n in sorted(names):
            out += _paragraph_texts(_read_zip_member(z, n), "p", "t")
    return "\n\n".join(out)


def _section_text(blocks, info, kind):
    starts, parts, pos = [], [], 0
    for title, text in blocks:
        starts.append((pos, kind, title, ""))
        parts.append(text)
        pos += len(text) + 2
    if info is not None:
        info["sections"] = starts
    return "\n\n".join(parts)


def _relationships(z, source, members):
    parent, name = posixpath.split(source)
    rels = posixpath.join(parent, "_rels", name + ".rels")
    if rels not in members:
        return {}
    out = {}
    for rel in ET.fromstring(_read_zip_member(z, rels)):
        if rel.get("TargetMode") == "External":
            continue
        target = posixpath.normpath(posixpath.join(parent, rel.get("Target", ""))).lstrip("/")
        if target in members:
            out[rel.get("Id")] = target
    return out


def extract_pptx(path: str, info=None) -> str:
    blocks = []
    with zipfile.ZipFile(path) as z:
        members = set(z.namelist())
        main = "ppt/presentation.xml"
        rels = _relationships(z, main, members)
        slides = []
        if main in members:
            for el in ET.fromstring(_read_zip_member(z, main)).iter():
                if _local(el.tag) == "sldId":
                    rid = next((v for k, v in el.attrib.items() if k.endswith("}id")), None)
                    if rid in rels:
                        slides.append(rels[rid])
        if not slides:
            slides = sorted((s for s in members if re.fullmatch(r"ppt/slides/slide\d+\.xml", s)),
                            key=lambda s: int(re.search(r"(\d+)\.xml$", s)[1]))
        for number, slide in enumerate(slides, 1):
            paragraphs = _paragraph_texts(_read_zip_member(z, slide), "p", "t")
            notes = next((n for n in _relationships(z, slide, members).values()
                          if n.startswith("ppt/notesSlides/")), None)
            if notes:
                paragraphs.extend(_paragraph_texts(_read_zip_member(z, notes), "p", "t"))
            blocks.append((str(number), "\n\n".join(paragraphs)))
    return _section_text(blocks, info, "slide")


def _xlsx_sheet_names(z: zipfile.ZipFile, names_set: set[str]) -> dict[str, str]:
    """Mappa 'xl/worksheets/sheet1.xml' -> nome del foglio, se disponibile."""
    if "xl/workbook.xml" not in names_set or "xl/_rels/workbook.xml.rels" not in names_set:
        return {}
    try:
        rid_to_target = _relationships(z, "xl/workbook.xml", names_set)
        wb_root = ET.fromstring(_read_zip_member(z, "xl/workbook.xml"))
        out: dict[str, str] = {}
        for el in wb_root.iter():
            if _local(el.tag) == "sheet":
                sname = el.attrib.get("name")
                rid = next((v for k, v in el.attrib.items() if _local(k) == "id"), None)
                if sname and rid and rid in rid_to_target:
                    out[rid_to_target[rid]] = sname
        return out
    except Exception:
        return {}


def extract_xlsx(path: str, info=None) -> str:
    """Estrae testo, numeri, valori calcolati e nomi dei fogli preservando le righe."""
    blocks = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        names_set = set(names)
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names_set:
            sroot = ET.fromstring(_read_zip_member(z, "xl/sharedStrings.xml"))
            for el in sroot.iter():
                if _local(el.tag) == "si":
                    shared.append("".join(t.text or "" for t in el.iter() if _local(t.tag) == "t"))
        sheet_map = _xlsx_sheet_names(z, names_set)
        sheets = [n for n in names if re.fullmatch(r"xl/worksheets/[^/]+\.xml", n)]
        sheets.sort(key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)])
        sheets = list(dict.fromkeys([*(n for n in sheet_map if n.startswith("xl/worksheets/")), *sheets]))
        for number, n in enumerate(sheets, 1):
            sheet_title = sheet_map.get(n)
            sheet_lines: list[str] = []
            root = ET.fromstring(_read_zip_member(z, n))
            for row in root.iter():
                if _local(row.tag) != "row":
                    continue
                cells: list[str] = []
                for c in row:
                    if _local(c.tag) != "c":
                        continue
                    ctype = c.attrib.get("t", "")
                    if ctype == "e":
                        continue  # errori formula (#REF!, #DIV/0!, ecc.)
                    val = ""
                    if ctype == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter() if _local(t.tag) == "t")
                    else:
                        v_el = next((ch for ch in c if _local(ch.tag) == "v"), None)
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
                    val = val.strip()
                    if val:
                        cells.append(val)
                if cells:
                    sheet_lines.append(" | ".join(cells))
            if sheet_lines:
                if sheet_title:
                    sheet_lines.insert(0, f"[{sheet_title}]")
                blocks.append((sheet_title or str(number), "\n".join(sheet_lines)))
        if not blocks and shared:
            return "\n".join(s for s in shared if s.strip())
    return _section_text(blocks, info, "sheet")


def extract_odf(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(_read_zip_member(z, "content.xml"))
    out = []
    for el in root.iter():
        if _local(el.tag) in ("p", "h"):
            line = "".join(el.itertext())
            if line.strip():
                out.append(line)
    separator = "\n\n" if str(path).lower().endswith(".odt") else "\n"
    return separator.join(out)
