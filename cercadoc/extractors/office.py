# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Estrattori per i formati Office/ODF (docx, pptx, xlsx, odt/ods/odp)."""
from __future__ import annotations

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
    for p in root.iter():
        if _local(p.tag) == para:
            line = "".join(t.text or "" for t in p.iter() if _local(t.tag) == text)
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
    return "\n".join(out)


def extract_pptx(path: str) -> str:
    out = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if re.fullmatch(r"ppt/(slides|notesSlides)/[^/]+\.xml", n)]
        names.sort(key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)])
        for n in names:
            out += _paragraph_texts(_read_zip_member(z, n), "p", "t")
    return "\n".join(out)


def _xlsx_sheet_names(z: zipfile.ZipFile, names_set: set[str]) -> dict[str, str]:
    """Mappa 'xl/worksheets/sheet1.xml' -> nome del foglio, se disponibile."""
    if "xl/workbook.xml" not in names_set or "xl/_rels/workbook.xml.rels" not in names_set:
        return {}
    try:
        rels_root = ET.fromstring(_read_zip_member(z, "xl/_rels/workbook.xml.rels"))
        rid_to_target: dict[str, str] = {}
        for el in rels_root.iter():
            if _local(el.tag) == "Relationship":
                rid = el.attrib.get("Id")
                target = el.attrib.get("Target", "")
                if rid and target:
                    target = target.lstrip("/")
                    if not target.startswith("xl/"):
                        target = "xl/" + target
                    rid_to_target[rid] = target
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


def extract_xlsx(path: str) -> str:
    """Estrae testo, numeri, valori calcolati e nomi dei fogli preservando le righe."""
    out: list[str] = []
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
        for n in sheets:
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
                    out.append(f"[{sheet_title}]")
                out.extend(sheet_lines)
        if not out and shared:
            out = [s for s in shared if s.strip()]
    return "\n".join(out)


def extract_odf(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(_read_zip_member(z, "content.xml"))
    out = []
    for el in root.iter():
        if _local(el.tag) in ("p", "h"):
            line = "".join(el.itertext())
            if line.strip():
                out.append(line)
    return "\n".join(out)
