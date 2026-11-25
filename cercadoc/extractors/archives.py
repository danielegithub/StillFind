# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Contenitori locali con un solo livello, limite cumulativo e percorsi temporanei generati."""
import posixpath
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

from ..errors import Stopped
from .office import MAX_ZIP_XML_BYTES
from .text import _decode

MAX_ENTRIES = 4096
PLAIN = {"txt", "md", "csv", "log", "json", "tex", "xml", "rtf", "html", "htm"}
OFFICE = {"docx", "xlsx", "pptx", "odt", "ods", "odp"}


class ArchiveLimit(ValueError):
    pass


def limit(cfg):
    return min(MAX_ZIP_XML_BYTES, max(1, int(cfg.get("max_file_mb", 50))) * 1024 * 1024)


def _safe(name):
    path = PurePosixPath(name)
    return bool(path.parts and not path.is_absolute() and ".." not in path.parts and
                "\\" not in name and ":" not in name and "\0" not in name)


class Members:
    def __init__(self, cfg, stop):
        self.cfg, self.stop = cfg, stop
        self.remaining = limit(cfg)
        self.chars = int(cfg["max_chars_per_file"])
        self.parts = []
        self.ocr = False
        self.failed = 0

    def consume(self, count):
        if self.stop is not None and self.stop.is_set():
            raise Stopped()
        if count > self.remaining:
            raise ArchiveLimit("Contenuto decompresso oltre il limite complessivo.")
        self.remaining -= count

    def append(self, name, text):
        piece = ("\n\n[" + name + "]\n" + text)[:self.chars]
        self.parts.append(piece)
        self.chars -= len(piece)

    def unreadable(self, name, exc):
        self.failed += 1
        self.append(name, "Elemento non letto · " + type(exc).__name__)

    def report(self, info):
        if self.failed and info is not None:
            info["partial_error"] = f"Lettura parziale · {self.failed} elementi non leggibili"

    def reserve_office(self, file):
        with zipfile.ZipFile(file) as inner:
            entries = inner.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ArchiveLimit("Troppi elementi nel documento Office.")
            self.consume(sum(e.file_size for e in entries if e.filename.lower().endswith((".xml", ".rels"))))

    def document(self, name, data, *, charged=False):
        from . import IMAGE_EXTS, extract
        ext = Path(name).suffix.lower().lstrip(".")
        if ext not in PLAIN | OFFICE | {"pdf", "p7m", "eml"} and not (self.cfg.get("ocr") and ext in IMAGE_EXTS):
            return
        if not charged:
            self.consume(len(data))
        with tempfile.TemporaryDirectory(prefix="cercadoc_member_") as tmp:
            # Mai usare il percorso del membro come destinazione sul filesystem.
            f = Path(tmp) / ("documento." + ext)
            f.write_bytes(data)
            cfg = dict(self.cfg, _inside_container=True, _archive_budget=self, max_chars_per_file=self.chars)
            text, used = extract(str(f), ext, cfg, stop=self.stop)
            self.ocr |= used
            self.append(name, text)


def _xml(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Dichiarazione XML non supportata nel libro.")
    return ET.fromstring(data)


def _unescape_path(name):
    return re.sub(rb"(?:%[0-9a-fA-F]{2})+",
                  lambda m: bytes.fromhex(m[0].replace(b"%", b"").decode()), name.encode()).decode()


def extract_archive(path, ext, cfg, stop, info=None):
    if cfg.get("_inside_container"):
        return "", False
    members = Members(cfg, stop)
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        if len(entries) > MAX_ENTRIES:
            raise ValueError("Archivio con troppi elementi.")
        def read(entry):
            members.consume(entry.file_size)
            with z.open(entry) as f:
                data = f.read(entry.file_size + 1)
            if len(data) != entry.file_size:
                raise ValueError("Dimensione del membro non valida.")
            return data

        if ext == "zip":
            from . import IMAGE_EXTS
            allowed = PLAIN | OFFICE | {"pdf", "p7m", "eml"}
            if cfg.get("ocr"):
                allowed |= IMAGE_EXTS
            for entry in entries:
                if not members.chars:
                    break
                if entry.is_dir() or not _safe(entry.filename):
                    continue
                suffix = Path(entry.filename).suffix.lower()
                if suffix in {".zip", ".epub"}:
                    continue
                if suffix.lstrip(".") not in allowed:
                    continue
                try:
                    members.document(entry.filename, read(entry), charged=True)
                except (ArchiveLimit, Stopped):
                    raise
                except Exception as exc:
                    members.unreadable(entry.filename, exc)
        else:
            def get(name):
                if not _safe(name):
                    raise ValueError("Percorso EPUB non valido.")
                return read(z.getinfo(name))
            container = _xml(get("META-INF/container.xml"))
            package = container.find(".//{*}rootfile")
            if package is None:
                raise ValueError("Descrizione EPUB assente.")
            opf_path = package.attrib["full-path"]
            opf = _xml(get(opf_path))
            manifest = {item.get("id"): item for item in opf.findall(".//{*}manifest/{*}item")}
            for ref in opf.findall(".//{*}spine/{*}itemref"):
                if not members.chars:
                    break
                item = manifest.get(ref.get("idref"))
                if item is None or item.get("media-type") not in ("application/xhtml+xml", "text/html"):
                    continue
                href = _unescape_path(item.attrib["href"].split("#")[0])
                member = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))
                from .mail import extract_html_text
                members.append(member, extract_html_text(_decode(get(member))))
    members.report(info)
    return "".join(members.parts), members.ocr
